"""
Migrate all data from Supabase to the new backend database.

Usage:
    1. Copy .env.migration.example to .env.migration and fill in values
    2. Dry run (preview):  python scripts/migrate_from_supabase.py --dry-run
    3. Execute migration:  python scripts/migrate_from_supabase.py
    4. Verify:             python scripts/migrate_from_supabase.py --verify

Requirements: pip install asyncpg httpx python-dotenv
"""

import asyncio
import argparse
import gzip
import os
import sys
import uuid

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv(".env.migration")
load_dotenv(".env", override=False)

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
NEW_DB_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


async def count_table(conn, table: str) -> int:
    row = await conn.fetchrow(f"SELECT count(*) as cnt FROM {table}")
    return row["cnt"]


async def dry_run():
    """Preview what will be migrated without writing anything."""
    print("=== DRY RUN — no data will be written ===\n")

    print("Connecting to Supabase DB...")
    src = await asyncpg.connect(SUPABASE_DB_URL)

    tables = {
        "auth.users": "SELECT count(*) as cnt FROM auth.users",
        "public.profiles": "SELECT count(*) as cnt FROM public.profiles",
        "public.plans": "SELECT count(*) as cnt FROM public.plans",
        "public.prompts": "SELECT count(*) as cnt FROM public.prompts",
        "public.series": "SELECT count(*) as cnt FROM public.series",
        "public.meetings": "SELECT count(*) as cnt FROM public.meetings",
    }

    total_transcripts = 0
    for table, query in tables.items():
        row = await src.fetchrow(query)
        cnt = row["cnt"]
        print(f"  {table}: {cnt} rows")

        if table == "public.meetings":
            row2 = await src.fetchrow(
                "SELECT count(*) as cnt FROM public.meetings WHERE transcript_json_path IS NOT NULL AND transcript_json IS NULL"
            )
            total_transcripts = row2["cnt"]
            print(f"    -> {total_transcripts} transcripts to download from S3")

    # Check optional tables
    for table in ["public.payment_methods", "public.payments_log"]:
        try:
            row = await src.fetchrow(f"SELECT count(*) as cnt FROM {table}")
            print(f"  {table}: {row['cnt']} rows")
        except Exception:
            print(f"  {table}: table not found (skipped)")

    # Check new DB connectivity
    print(f"\nNew DB URL: {NEW_DB_URL[:40]}...")
    try:
        dst = await asyncpg.connect(NEW_DB_URL)
        existing = await count_table(dst, "users")
        print(f"  New DB reachable. Existing users: {existing}")
        await dst.close()
    except Exception as e:
        print(f"  New DB connection FAILED: {e}")

    await src.close()
    print("\n=== Dry run complete. Run without --dry-run to execute. ===")


async def migrate():
    """Execute full migration."""
    print("Connecting to Supabase DB...")
    src = await asyncpg.connect(SUPABASE_DB_URL)

    print("Connecting to new DB...")
    dst = await asyncpg.connect(NEW_DB_URL)

    stats = {}

    # --- 1. Plans (before profiles, because profiles reference plans) ---
    print("\n=== Migrating plans ===")
    plans = await src.fetch("SELECT * FROM public.plans")
    migrated = 0
    for p in plans:
        try:
            await dst.execute("""
                INSERT INTO plans (id, name, description, minutes_limit, price_rub, duration_days, active, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (id) DO NOTHING
            """, p["id"], p["name"], p.get("description"), p.get("minutes_limit"),
                p.get("price_rub", 0), p.get("duration_days", 30), p.get("active", True),
                p.get("created_at"), p.get("updated_at"))
            migrated += 1
        except Exception as e:
            print(f"  Error migrating plan {p['id']}: {e}")
    stats["plans"] = migrated
    print(f"Migrated {migrated}/{len(plans)} plans")

    # --- 2. Users ---
    print("\n=== Migrating users ===")
    users = await src.fetch("""
        SELECT id, email, encrypted_password,
               raw_app_meta_data->>'provider' as provider,
               created_at
        FROM auth.users
    """)
    migrated = 0
    for u in users:
        try:
            provider = u["provider"] or "email"
            await dst.execute("""
                INSERT INTO users (id, email, encrypted_password, auth_provider, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO NOTHING
            """, u["id"], u["email"], u["encrypted_password"], provider, u["created_at"])
            migrated += 1
        except Exception as e:
            print(f"  Error migrating user {u['email']}: {e}")
    stats["users"] = migrated
    print(f"Migrated {migrated}/{len(users)} users")

    # --- 3. Profiles ---
    print("\n=== Migrating profiles ===")
    profiles = await src.fetch("SELECT * FROM public.profiles")
    migrated = 0
    for p in profiles:
        try:
            user_id = p.get("user_id") or p["id"]
            await dst.execute("""
                INSERT INTO profiles (id, user_id, full_name, company_name, position, avatar_url,
                    current_plan_id, subscription_active_until, free_minutes_used,
                    paid_minutes_used_this_cycle, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (id) DO NOTHING
            """, p["id"], user_id, p.get("full_name"), p.get("company_name"),
                p.get("position"), p.get("avatar_url"), p.get("current_plan_id"),
                p.get("subscription_active_until"), p.get("free_minutes_used", 0),
                p.get("paid_minutes_used_this_cycle", 0),
                p.get("created_at"), p.get("updated_at"))
            migrated += 1
        except Exception as e:
            print(f"  Error migrating profile {p['id']}: {e}")
    stats["profiles"] = migrated
    print(f"Migrated {migrated}/{len(profiles)} profiles")

    # --- 4. Prompts ---
    print("\n=== Migrating prompts ===")
    prompts = await src.fetch("SELECT * FROM public.prompts")
    migrated = 0
    for p in prompts:
        try:
            await dst.execute("""
                INSERT INTO prompts (id, name, description, prompt_text, type, version, model,
                    is_active, use_case, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (id) DO NOTHING
            """, p["id"], p["name"], p.get("description"), p.get("prompt_text", ""),
                p.get("type", "system"), p.get("version", 1), p.get("model", "deepseek-chat"),
                p.get("is_active", True), p.get("use_case"),
                p.get("created_at"), p.get("updated_at"))
            migrated += 1
        except Exception as e:
            print(f"  Error migrating prompt {p['id']}: {e}")
    stats["prompts"] = migrated
    print(f"Migrated {migrated}/{len(prompts)} prompts")

    # --- 5. Series ---
    print("\n=== Migrating series ===")
    series = await src.fetch("SELECT * FROM public.series")
    migrated = 0
    for s in series:
        try:
            await dst.execute("""
                INSERT INTO series (id, user_id, name, description, color, icon,
                    is_archived, sort_order, created_at, updated_at, archived_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (id) DO NOTHING
            """, s["id"], s["user_id"], s["name"], s.get("description"),
                s.get("color", "#3B82F6"), s.get("icon", "\U0001F4C1"),
                s.get("is_archived", False), s.get("sort_order", 0),
                s.get("created_at"), s.get("updated_at"), s.get("archived_at"))
            migrated += 1
        except Exception as e:
            print(f"  Error migrating series {s['id']}: {e}")
    stats["series"] = migrated
    print(f"Migrated {migrated}/{len(series)} series")

    # --- 6. Meetings + Transcripts from S3 ---
    print("\n=== Migrating meetings + downloading transcripts from S3 ===")
    meetings = await src.fetch("SELECT * FROM public.meetings")
    print(f"Found {len(meetings)} meetings")

    migrated = 0
    transcript_count = 0
    transcript_errors = 0

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        for i, m in enumerate(meetings):
            transcript_json = m.get("transcript_json")
            transcript_json_path = m.get("transcript_json_path")

            # Download transcript from S3 if needed
            if transcript_json_path and not transcript_json:
                try:
                    url = f"{SUPABASE_URL}/storage/v1/object/transcripts/{transcript_json_path}"
                    resp = await http_client.get(url, headers={
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "apikey": SUPABASE_SERVICE_KEY,
                    })
                    if resp.status_code == 200:
                        data = resp.content
                        if transcript_json_path.endswith(".gz"):
                            data = gzip.decompress(data)
                        transcript_json = data.decode("utf-8")
                        transcript_count += 1
                    else:
                        print(f"  Warning: S3 returned {resp.status_code} for {transcript_json_path}")
                        transcript_errors += 1
                except Exception as e:
                    print(f"  Warning: failed to download transcript for {m['id']}: {e}")
                    transcript_errors += 1

            try:
                await dst.execute("""
                    INSERT INTO meetings (id, user_id, title, status, duration_seconds,
                        local_filename, transcript, transcript_json, protocol, tasks_json,
                        series_id, prompt_id, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (id) DO NOTHING
                """, m["id"], m["user_id"], m.get("title"), m.get("status", "pending_upload"),
                    m.get("duration_seconds"), m.get("local_filename"),
                    m.get("transcript"), transcript_json,
                    m.get("protocol"), m.get("tasks_json"),
                    m.get("series_id"), m.get("prompt_id"),
                    m.get("created_at"), m.get("updated_at"))
                migrated += 1
            except Exception as e:
                print(f"  Error migrating meeting {m['id']}: {e}")

            if (i + 1) % 50 == 0:
                print(f"  Processed {i + 1}/{len(meetings)}... ({transcript_count} transcripts downloaded)")

    stats["meetings"] = migrated
    stats["transcripts_downloaded"] = transcript_count
    stats["transcript_errors"] = transcript_errors
    print(f"Migrated {migrated}/{len(meetings)} meetings")
    print(f"  Transcripts downloaded from S3: {transcript_count}")
    if transcript_errors > 0:
        print(f"  Transcript download errors: {transcript_errors}")

    # --- 7. Payment data ---
    print("\n=== Migrating payment data ===")
    for table, insert_sql, fields in [
        ("payment_methods",
         "INSERT INTO payment_methods (id, user_id, payment_method_id, last_used_at, created_at) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
         lambda pm: (pm.get("id", uuid.uuid4()), pm["user_id"], pm["payment_method_id"], pm.get("last_used_at"), pm.get("created_at"))),
        ("payments_log",
         "INSERT INTO payments_log (id, user_id, yookassa_payment_id, status, amount, plan_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (id) DO NOTHING",
         lambda pl: (pl.get("id", uuid.uuid4()), pl["user_id"], pl["yookassa_payment_id"], pl["status"], pl["amount"], pl.get("plan_id"), pl.get("created_at"))),
    ]:
        try:
            rows = await src.fetch(f"SELECT * FROM public.{table}")
            count = 0
            for row in rows:
                try:
                    await dst.execute(insert_sql, *fields(row))
                    count += 1
                except Exception as e:
                    print(f"  Error in {table}: {e}")
            stats[table] = count
            print(f"  {table}: {count}/{len(rows)} migrated")
        except Exception as e:
            print(f"  {table}: skipped ({e})")
            stats[table] = 0

    await src.close()
    await dst.close()

    # --- Summary ---
    print("\n" + "=" * 50)
    print("MIGRATION SUMMARY")
    print("=" * 50)
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print("=" * 50)
    print("Migration complete!")


async def verify():
    """Verify migration by comparing row counts."""
    print("=== VERIFICATION — comparing row counts ===\n")

    src = await asyncpg.connect(SUPABASE_DB_URL)
    dst = await asyncpg.connect(NEW_DB_URL)

    checks = [
        ("auth.users", "users"),
        ("public.profiles", "profiles"),
        ("public.plans", "plans"),
        ("public.prompts", "prompts"),
        ("public.series", "series"),
        ("public.meetings", "meetings"),
    ]

    all_ok = True
    for src_table, dst_table in checks:
        try:
            src_count = (await src.fetchrow(f"SELECT count(*) as cnt FROM {src_table}"))["cnt"]
        except Exception:
            src_count = "N/A"
        try:
            dst_count = (await dst.fetchrow(f"SELECT count(*) as cnt FROM {dst_table}"))["cnt"]
        except Exception:
            dst_count = "N/A"

        match = "OK" if src_count == dst_count else "MISMATCH"
        if match == "MISMATCH":
            all_ok = False
        print(f"  {src_table} -> {dst_table}: {src_count} -> {dst_count} [{match}]")

    # Check transcripts were downloaded
    src_with_path = (await src.fetchrow(
        "SELECT count(*) as cnt FROM public.meetings WHERE transcript_json_path IS NOT NULL"
    ))["cnt"]
    dst_with_json = (await dst.fetchrow(
        "SELECT count(*) as cnt FROM meetings WHERE transcript_json IS NOT NULL AND transcript_json != ''"
    ))["cnt"]
    print(f"\n  Transcripts in S3 (Supabase): {src_with_path}")
    print(f"  Transcripts in DB (new):      {dst_with_json}")
    if src_with_path > dst_with_json:
        print(f"  WARNING: {src_with_path - dst_with_json} transcripts missing!")
        all_ok = False

    await src.close()
    await dst.close()

    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED — review above'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate data from Supabase to new backend")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--verify", action="store_true", help="Verify migration after completion")
    args = parser.parse_args()

    if args.dry_run:
        asyncio.run(dry_run())
    elif args.verify:
        asyncio.run(verify())
    else:
        asyncio.run(migrate())
