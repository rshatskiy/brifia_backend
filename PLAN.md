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

### Фаза 2 — Стабилизация загрузки ⬜
- [ ] Сохранять состояние загрузок в PostgreSQL вместо `active_uploads` dict в памяти
- [ ] На клиенте: заменить SharedPreferences на SQLite (drift) для атомарных обновлений
- [ ] Идемпотентный `/initiate` — повторный вызов с тем же meeting_id возвращает существующий upload_id
- [ ] Клиент использует `GET /uploads/{id}/status` как source of truth при восстановлении
- [ ] На iOS: использовать `Isolate.run()` вместо inline загрузки в main isolate

### Фаза 3 — Миграция Flutter на новый API ⬜
- [ ] Создать `lib/core/api_client.dart` — HTTP-клиент с auto-refresh JWT
- [ ] Переписать `AuthController` — свой API вместо `supabase.auth.*`
- [ ] Переписать `MeetingsRepository` — REST API + WebSocket вместо Supabase
- [ ] Переписать `SeriesRepository` — REST API
- [ ] Переписать `PromptsRepository` — REST API
- [ ] Переписать `AccountScreen` — REST API для профиля и плана
- [ ] Переписать `SubscriptionDetailsBottomSheet` — REST API для оплаты
- [ ] Переписать `BackgroundUploadService` — новый бекенд вместо Supabase для токенов/статусов
- [ ] Убрать `supabase_flutter` из pubspec.yaml
- [ ] Удалить `lib/config/supabase_config.dart`, `supabaseClientProvider`

### Фаза 4 — Веб-версия (Next.js) ⬜
- [ ] Инициализация проекта Next.js
- [ ] Авторизация (вход/регистрация)
- [ ] Личный кабинет (профиль, текущий план, история платежей)
- [ ] Оплата и управление подпиской (YooKassa)
- [ ] Лендинг

### Фаза 5 — Миграция данных из Supabase ⬜
- [ ] Экспорт пользователей из `auth.users` (bcrypt-хэши совместимы)
- [ ] Экспорт таблиц: profiles, meetings, series, prompts, plans
- [ ] Скачать transcript JSON из Supabase Storage (S3) → вставить в `meetings.transcript_json`
- [ ] Экспорт платёжных данных: payment_methods, payments_log
- [ ] Скрипт миграции готов: `scripts/migrate_from_supabase.py`

### Фаза 6 — Деплой и переключение ⬜
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
