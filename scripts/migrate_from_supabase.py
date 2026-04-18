"""
Migrate all data from Supabase to the new backend database.

Usage:
    1. Set environment variables (or create .env):
       - SUPABASE_DB_URL: direct PostgreSQL connection to Supabase
         (find in Supabase Dashboard > Settings > Database > Connection string)
       - DATABASE_URL: new backend database (standard asyncpg format)
       - SUPABASE_URL: Supabase API URL (for storage download)
       - SUPABASE_SERVICE_KEY: Supabase service_role key (for storage access)

    2. Run: python scripts/migrate_from_supabase.py
"""

import asyncio
import gzip
import os
import sys
import uuid

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")  # postgresql://... (direct, not pooler)
NEW_DB_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


async def migrate():
    print("Connecting to Supabase DB...")
    src = await asyncpg.connect(SUPABASE_DB_URL)

    print("Connecting to new DB...")
    dst = await asyncpg.connect(NEW_DB_URL)

    # --- 1. Users ---
    print("\n=== Migrating users ===")
    users = await src.fetch("""
        SELECT id, email, encrypted_password,
               raw_app_meta_data->>'provider' as provider,
               created_at
        FROM auth.users
    """)
    print(f"Found {len(users)} users")

    for u in users:
        provider = u["provider"] or "email"
        await dst.execute("""
            INSERT INTO users (id, email, encrypted_password, auth_provider, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
        """, u["id"], u["email"], u["encrypted_password"], provider, u["created_at"])
    print(f"Migrated {len(users)} users")

    # --- 2. Plans ---
    print("\n=== Migrating plans ===")
    plans = await src.fetch("SELECT * FROM public.plans")
    for p in plans:
        await dst.execute("""
            INSERT INTO plans (id, name, description, minutes_limit, price_rub, duration_days, active, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO NOTHING
        """, p["id"], p["name"], p.get("description"), p.get("minutes_limit"),
            p.get("price_rub", 0), p.get("duration_days", 30), p.get("active", True),
            p.get("created_at"), p.get("updated_at"))
    print(f"Migrated {len(plans)} plans")

    # --- 3. Profiles ---
    print("\n=== Migrating profiles ===")
    profiles = await src.fetch("SELECT * FROM public.profiles")
    for p in profiles:
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
    print(f"Migrated {len(profiles)} profiles")

    # --- 4. Prompts ---
    print("\n=== Migrating prompts ===")
    prompts = await src.fetch("SELECT * FROM public.prompts")
    for p in prompts:
        await dst.execute("""
            INSERT INTO prompts (id, name, description, prompt_text, type, version, model,
                is_active, use_case, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id) DO NOTHING
        """, p["id"], p["name"], p.get("description"), p.get("prompt_text", ""),
            p.get("type", "system"), p.get("version", 1), p.get("model", "deepseek-chat"),
            p.get("is_active", True), p.get("use_case"),
            p.get("created_at"), p.get("updated_at"))
    print(f"Migrated {len(prompts)} prompts")

    # --- 5. Series ---
    print("\n=== Migrating series ===")
    series = await src.fetch("SELECT * FROM public.series")
    for s in series:
        await dst.execute("""
            INSERT INTO series (id, user_id, name, description, color, icon,
                is_archived, sort_order, created_at, updated_at, archived_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id) DO NOTHING
        """, s["id"], s["user_id"], s["name"], s.get("description"),
            s.get("color", "#3B82F6"), s.get("icon", "\U0001F4C1"),
            s.get("is_archived", False), s.get("sort_order", 0),
            s.get("created_at"), s.get("updated_at"), s.get("archived_at"))
    print(f"Migrated {len(series)} series")

    # --- 6. Meetings + Transcripts from S3 ---
    print("\n=== Migrating meetings ===")
    meetings = await src.fetch("SELECT * FROM public.meetings")
    print(f"Found {len(meetings)} meetings")

    transcript_count = 0
    async with httpx.AsyncClient(timeout=60.0) as http:
        for i, m in enumerate(meetings):
            # Download transcript JSON from Supabase Storage if path exists
            transcript_json = m.get("transcript_json")
            transcript_json_path = m.get("transcript_json_path")

            if transcript_json_path and not transcript_json:
                try:
                    url = f"{SUPABASE_URL}/storage/v1/object/transcripts/{transcript_json_path}"
                    resp = await http.get(url, headers={
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "apikey": SUPABASE_SERVICE_KEY,
                    })
                    if resp.status_code == 200:
                        data = resp.content
                        if transcript_json_path.endswith(".gz"):
                            data = gzip.decompress(data)
                        transcript_json = data.decode("utf-8")
                        transcript_count += 1
                except Exception as e:
                    print(f"  Warning: failed to download transcript for {m['id']}: {e}")

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

            if (i + 1) % 50 == 0:
                print(f"  Processed {i + 1}/{len(meetings)} meetings...")

    print(f"Migrated {len(meetings)} meetings ({transcript_count} transcripts downloaded from S3)")

    # --- 7. Payment data ---
    print("\n=== Migrating payment data ===")
    try:
        payment_methods = await src.fetch("SELECT * FROM public.payment_methods")
        for pm in payment_methods:
            await dst.execute("""
                INSERT INTO payment_methods (id, user_id, payment_method_id, last_used_at, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO NOTHING
            """, pm.get("id", uuid.uuid4()), pm["user_id"],
                pm["payment_method_id"], pm.get("last_used_at"), pm.get("created_at"))
        print(f"Migrated {len(payment_methods)} payment methods")
    except Exception as e:
        print(f"  Skipped payment_methods: {e}")

    try:
        payments_log = await src.fetch("SELECT * FROM public.payments_log")
        for pl in payments_log:
            await dst.execute("""
                INSERT INTO payments_log (id, user_id, yookassa_payment_id, status, amount, plan_id, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO NOTHING
            """, pl.get("id", uuid.uuid4()), pl["user_id"],
                pl["yookassa_payment_id"], pl["status"], pl["amount"],
                pl.get("plan_id"), pl.get("created_at"))
        print(f"Migrated {len(payments_log)} payment logs")
    except Exception as e:
        print(f"  Skipped payments_log: {e}")

    await src.close()
    await dst.close()
    print("\n=== Migration complete! ===")


if __name__ == "__main__":
    asyncio.run(migrate())
