# План миграции Brifia

## Фазы

### Фаза 0 — Новый бекенд (FastAPI + PostgreSQL) ✅
- [x] Структура проекта
- [x] Модели БД (users, profiles, meetings, series, prompts, plans, payments)
- [x] Auth эндпоинты (register, login, refresh, OAuth Google/Apple, verify, delete)
- [x] CRUD эндпоинты (meetings, series, prompts, profiles, plans)
- [x] WebSocket для realtime обновлений митингов
- [x] Платёжные эндпоинты (YooKassa create/webhook/cancel)
- [x] Внутренний API для faster-whisper (API key auth)
- [x] Alembic миграции
- [x] Репозиторий на GitHub: rshatskiy/brifia_backend

### Фаза 1 — Адаптация faster-whisper ✅
- [x] Заменить Supabase auth на вызов `/auth/verify` нового бекенда
- [x] Заменить `supabase.table('meetings').update()` на `PUT /internal/meetings/{id}`
- [x] Убрать загрузку в Supabase Storage — transcript_json прямо в БД
- [x] Заменить `get_meeting_status` на `GET /internal/meetings/{id}/status`
- [x] Обновить `.env` (BACKEND_URL, BACKEND_API_KEY вместо SUPABASE_*)
- [x] **Dual-mode**: Supabase как fallback на переходный период
  - verify_token: новый бекенд → fallback Supabase
  - update_meeting_data: пишет в оба бекенда (transcript_json только в новый)
  - get_meeting_status: новый бекенд → fallback Supabase
  - Supabase включается при наличии SUPABASE_URL/KEY в .env, иначе работает только новый бекенд

### Фаза 2 — Стабилизация загрузки (серверная часть) ✅
- [x] Модель `Upload` в PostgreSQL — source of truth для состояния загрузок
- [x] `/internal/uploads/*` эндпоинты: create, get, record chunk, mark assembling, update status
- [x] Идемпотентный create_or_resume: повторный вызов с тем же meeting_id возвращает существующий upload
- [x] faster-whisper: `_persist_upload()` сохраняет каждое изменение состояния в БД
- [x] faster-whisper: статусы assembling/processing/completed/error персистятся в PostgreSQL
- [ ] На клиенте: заменить SharedPreferences на SQLite (drift) — **Фаза 3**
- [ ] На iOS: `Isolate.run()` вместо inline загрузки — **Фаза 3**

### Фаза 3 — Миграция Flutter на новый API ✅
- [x] `lib/config/api_config.dart` — конфиг нового бекенда
- [x] `lib/core/api_client.dart` — HTTP-клиент с auto-refresh JWT
- [x] `lib/core/websocket_manager.dart` — WebSocket для realtime обновлений
- [x] `lib/core/audio_file_manager.dart` — управление аудиофайлами + скачивание/экспорт
- [x] `api_auth_controller.dart` — новый auth (email, Google, Apple OAuth)
- [x] `api_meetings_repository.dart` — meetings CRUD + WebSocket realtime
- [x] `api_series_repository.dart` — series CRUD
- [x] `api_prompts_repository.dart` — prompts read
- [x] `AuthState` — заменить Supabase `User` на `AppUser` (без зависимости от Supabase)
- [x] `AuthController` — полный rewrite на ApiClient (email, Google, Apple OAuth)
- [x] `main.dart` — заменить `Supabase.initialize()` на `ApiClient.loadTokens()`
- [x] `BackgroundUploadService` — убрать `SupabaseClient` из конструктора
- [x] Переключить `meetingsRepositoryProvider` → `ApiMeetingsRepository` + WebSocket
- [x] Переписать `SeriesRepository` на ApiClient REST
- [x] Переписать `PromptsRepository` на ApiClient REST
- [x] `Series.fromJson` — поддержка snake_case и camelCase
- [x] `ApiMeetingsRepository` — все методы для UI контроллеров
- [x] `AccountScreen` — профиль через `/api/v1/profiles/me`, планы через `/api/v1/plans`
- [x] `AccountScreen` — оплата через `/api/v1/payments/create`, веб-сессия
- [x] `SubscriptionDetailsBottomSheet` — отмена через `/api/v1/payments/cancel`, `AppUser` вместо Supabase `User`
- [x] `RecordingScreen` — убран `check-user-access` Edge Function
- [x] `meeting_detail_screen.dart` — transcript загрузка через API, profile через API
- [x] Bitrix24 OAuth — собственный бекенд: модель `BitrixIntegration`, миграция `20260501_0001`, роутер `/api/v1/bitrix/{oauth/init,oauth/callback,oauth/success,oauth/error,oauth/refresh,credentials,status}`. HMAC-подписанный state в callback. Клиент: `_connectBitrix`, `_fetchAndUpdateBitrixCredentialsFromSupabase`, `_refreshBitrixToken` через `ApiClient`. WebView ловит `$apiBaseUrl/api/v1/bitrix/oauth/{success,error}`. Удалён мёртвый `ExportTasksDialog` (~390 строк).
- [x] `main.dart` — убраны все `Supabase.instance` ссылки
- [x] `recording_screen.dart` — убран `check-user-access` Edge Function
- [x] `callbackDispatcher` — token из SharedPreferences, refresh через `/auth/refresh`, статус через API
- [x] `supabase_flutter` убран из pubspec.yaml
- [x] **0 ссылок на `Supabase.instance` во всём проекте**

### Фаза 4 — Веб-версия (Next.js) ✅
- [x] Инициализация проекта Next.js + Tailwind + TypeScript
- [x] API-клиент с типизацией (`src/lib/api.ts`)
- [x] Auth store на Zustand + cookies (`src/lib/auth.ts`)
- [x] Лендинг с описанием фич
- [x] Авторизация — вход и регистрация
- [x] Личный кабинет — профиль, план, использование минут
- [x] Оплата — создание платежа YooKassa, редирект, success/cancel страницы
- [x] Отмена подписки
- [x] Репозиторий на GitHub: rshatskiy/brifia_web

### Фаза 5 — Миграция данных из Supabase ✅
- [x] Скрипт миграции: `scripts/migrate_from_supabase.py`
- [x] `--dry-run` — превью количества строк без записи
- [x] `--verify` — проверка целостности после миграции
- [x] Экспорт пользователей из `auth.users` (bcrypt-хэши совместимы)
- [x] Экспорт таблиц: plans → users → profiles → prompts → series → meetings
- [x] Скачивание transcript JSON из Supabase S3 → `meetings.transcript_json`
- [x] Экспорт платёжных данных: payment_methods, payments_log
- [x] Обработка ошибок per-row (не останавливается на одной ошибке)
- [x] `.env.migration.example` — шаблон конфигурации

### Фаза 6 — Деплой и переключение ⬜ (следующий шаг)
- [ ] Развернуть новый бекенд на удалённом сервере (VPS)
- [ ] Настроить PostgreSQL на сервере
- [ ] Настроить SSL/домен для API
- [ ] Настроить faster-whisper для работы с удалённым бекендом
- [ ] Запустить миграцию данных
- [ ] Переключить Flutter-приложение на новый API
- [ ] Отключить Supabase

## Архитектура

```
Flutter App ──→ Новый бекенд (FastAPI, удалённый VPS)
                    │
Next.js Web ──→     │  REST + WebSocket + платежи
                    │
                    └──→ faster-whisper (локальный компьютер)
                              chunked upload + транскрибация
```

## Ключевые решения
- Транскрипты хранятся в `meetings.transcript_json` (TEXT) — без отдельного S3
- faster-whisper общается с бекендом через `/internal/*` (API key, не JWT)
- Bcrypt-хэши паролей совместимы — миграция пользователей без сброса паролей
- WebSocket `/ws/meetings?token=xxx` — push при изменении митинга
