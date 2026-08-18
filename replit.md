# جدول أيزنهاور

تطبيق عربي لإدارة المهام اليومية حسب الأولوية، مع التكرار التلقائي والتنبيهات والإحصائيات وجدول التمارين.

## Run & Operate

- `python app.py` — تشغيل نسخة Flask المحسنة محليًا
- `gunicorn --workers 1 --threads 4 app:app` — تشغيل نسخة الإنتاج
- `pnpm --filter @workspace/api-server run dev` — تشغيل خادم الـ API التجريبي في القالب
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- متغيرات اختيارية: `DB_PATH`, `TIMEZONE` (الافتراضي `Africa/Cairo`), `PUBLIC_URL`, `NTFY_TOPIC`, `NTFY_ENABLED`, `AUTO_DAILY_TASKS`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `app.py` — التطبيق الكامل، صفحات الويب، قاعدة البيانات، والمجدول
- `scheduler.db` — قاعدة SQLite التي ينشئها التطبيق في بيئة الإنتاج
- `attached_assets/scheduler_1787024656030.db` — نسخة قاعدة البيانات المرفوعة، وتُستخدم تلقائيًا محليًا إذا لم توجد قاعدة في الجذر
- `Procfile` و`requirements.txt` — إعداد تشغيل Render/Gunicorn

## Architecture decisions

- التطبيق Flask بسيط بقاعدة SQLite حتى يبقى متوافقًا مع نشر Render الحالي.
- المهام اليومية تُضاف عند تشغيل التطبيق وعند بداية اليوم، مع زر يدوي آمن لا يكررها.
- المنطقة الزمنية قابلة للضبط، والافتراضي Africa/Cairo بدل الاعتماد على توقيت الخادم.
- إشعارات ntfy اختيارية؛ فشل الإشعار لا يوقف التطبيق أو عمليات المهام.

## Product

إضافة وتعديل وحذف المهام، تصنيفها حسب مصفوفة أيزنهاور، تسجيل الإنجاز أو التأخير أو التخطي، التكرار اليومي/الأسبوعي/الشهري، التنظيف، الإحصائيات، ووضع التركيز.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
