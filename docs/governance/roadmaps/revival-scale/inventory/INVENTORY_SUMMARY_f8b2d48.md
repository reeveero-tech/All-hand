# ملخص جرد الملفات — اللقطة `f8b2d48` (R-155)

> **مُولَّد آلياً** من `scripts/build_file_inventory.py` — لا يُحرَّر يدوياً. أعد الإنتاج بالأمر:
>
> `python3 scripts/build_file_inventory.py . f8b2d4828ee5aba3dc097665199f1fdf6450c2eb docs/governance/roadmaps/revival-scale/inventory/FILE_INVENTORY_f8b2d48.csv --coverage docs/audit/M8/logs/raw/053_r155_coverage_files.json --review-date 2026-10-17 --summary docs/governance/roadmaps/revival-scale/inventory/INVENTORY_SUMMARY_f8b2d48.json --summary-md docs/governance/roadmaps/revival-scale/inventory/INVENTORY_SUMMARY_f8b2d48.md`

- **اللقطة المرجعية**: `f8b2d4828ee5aba3dc097665199f1fdf6450c2eb` — قائمة المسارات = `git ls-files -z` عند هذا الـSHA (مقروءة من كائنات git لا من شجرة العمل).
- **ملف الجرد**: [`FILE_INVENTORY_f8b2d48.csv`](FILE_INVENTORY_f8b2d48.csv) — صف واحد لكل ملف متعقب.
- **إجمالي الملفات المتعقبة**: **1867**؛ صفوف الجرد: **1867** (فحص ذاتي: عدد الصفوف = عدد `git ls-files`، مجموعة المسارات متطابقة، لا تكرار، لا قرار `DELETE`).
- **ملفات حُسم دورها بدليل** (حالة ≠ `unresolved`): **1850** — المراجعة **آلية**: رسم استيراد AST، مراجع git/CI/Docker/Render، أنماط التحميل الديناميكي، تغطية تنفيذ pytest عند اللقطة. **ليست** قراءة بشرية سطراً بسطر لكل ملف، ولا تثبت صحة سلوك الملف.
- **صفوف غير محسومة**: **17** — لذلك **لا يُعلن فهم كامل للمستودع**.
- ملفات لها بيانات تغطية تنفيذ: 1146.

## التوزيع

| البعد | القيم |
|---|---|
| الحالة | `active`=131، `docs-only`=61، `generated`=563، `legacy-compatible`=1000، `shared`=36، `test-only`=59، `unresolved`=17 |
| المخاطر | `high`=57، `low`=1693، `medium`=117 |
| القرار (بلا DELETE) | `ADAPTER`=1000، `DOCUMENT`=78، `KEEP`=765، `UPGRADE`=24 |
| الثقة | `high`=1847، `low`=17، `medium`=3 |
| نوع الملف | `ci-workflow`=2، `css`=2، `csv`=2، `dependency-lock`=5، `dependency-manifest`=5، `docker`=2، `env-template`=1، `git-config`=1، `html`=1، `image`=7، `ini`=1، `javascript`=5، `json`=32، `jsonl`=3، `jsx`=8، `log`=518، `markdown`=56، `python`=1188، `render-config`=1، `shell`=2، `sql`=4، `text`=4، `tsx`=8، `typescript`=9 |

## قابلية الوصول من نقاط الدخول (عدد الملفات)

| الجذر | الملفات |
|---|---|
| deploy-server | 90 |
| web-build | 11 |
| root-frontend-build | 17 |
| cli/ci | 136 |
| tests | 1143 |

## المخاطر العالية حسب المكون

| المكون | عدد `high` |
|---|---|
| scripts/tools | 18 |
| dependencies | 8 |
| deployment | 4 |
| frontend-web (deployed) | 3 |
| agent_civilization/agents/executors | 2 |
| agent_civilization/core | 2 |
| agent_civilization/specialists | 2 |
| agent_civilization/specialists-templates | 2 |
| agent_civilization/storage | 2 |
| ci | 2 |
| scripts/r042 | 2 |
| agos_kernel/infrastructure | 1 |
| api-server | 1 |
| docs/sql | 1 |
| frontend-root (TanStack Start) | 1 |
| scripts/bug01 | 1 |
| scripts/bug02 | 1 |
| scripts/bug03 | 1 |
| scripts/bug04 | 1 |
| scripts/sec04 | 1 |
| scripts/sec06 | 1 |

## المكونات

| المكون | الملفات |
|---|---|
| agent_civilization | 2 |
| agent_civilization/agents | 2 |
| agent_civilization/agents/executors | 2 |
| agent_civilization/agents/infrastructure | 4 |
| agent_civilization/agents/real | 3 |
| agent_civilization/communication | 1 |
| agent_civilization/core | 7 |
| agent_civilization/core/agents | 5 |
| agent_civilization/pipeline | 2 |
| agent_civilization/registry | 2 |
| agent_civilization/routing | 2 |
| agent_civilization/runtime | 1 |
| agent_civilization/specialists | 1021 |
| agent_civilization/specialists-templates | 23 |
| agent_civilization/storage | 2 |
| agent_civilization/utils | 1 |
| agos_kernel/agent_production | 4 |
| agos_kernel/infrastructure | 5 |
| api-server | 1 |
| ci | 2 |
| cli-runner | 1 |
| dependencies | 8 |
| deployment | 4 |
| docs/audit/M0 | 17 |
| docs/audit/M1 | 5 |
| docs/audit/M2 | 1 |
| docs/audit/M3 | 12 |
| docs/audit/M4 | 19 |
| docs/audit/M5 | 19 |
| docs/audit/M6 | 229 |
| docs/audit/M7 | 231 |
| docs/audit/M8 | 51 |
| docs/governance | 18 |
| docs/governance/revival-scale-package | 24 |
| docs/sql | 2 |
| frontend-root (TanStack Start) | 24 |
| frontend-web (deployed) | 16 |
| repo-root | 9 |
| scripts/bug01 | 1 |
| scripts/bug02 | 1 |
| scripts/bug03 | 1 |
| scripts/bug04 | 1 |
| scripts/r042 | 5 |
| scripts/sec04 | 1 |
| scripts/sec05 | 1 |
| scripts/sec06 | 1 |
| scripts/tools | 23 |
| tests | 50 |

## الملفات غير المحسومة (كاملة)

### المجموعة: frontend-root (TanStack Start) (17)

- **سبب عدم الحسم**: root TanStack Start app is built+linted by CI but NOT deployed by Dockerfile/render.yaml (they build web/); vite.config.ts targets nitro/cloudflare and .lovable/ exists, so an external deployment cannot be ruled in or out from the repository — fate is open question Q-002
- **الخطوة اللازمة**: owner answers Q-002 (is src/ deployed anywhere, e.g. Lovable/Cloudflare?) then R-158 records active vs legacy; until then KEEP + keep CI build green
- **الملفات**:
  - `src/components/AppShell.tsx`
  - `src/hooks/useBackend.ts`
  - `src/lib/api.ts`
  - `src/lib/error-capture.ts`
  - `src/lib/error-page.ts`
  - `src/lib/lovable-error-reporting.ts`
  - `src/router.tsx`
  - `src/routes/__root.tsx`
  - `src/routes/agents.tsx`
  - `src/routes/chat.tsx`
  - `src/routes/command.tsx`
  - `src/routes/index.tsx`
  - `src/routes/settings.tsx`
  - `src/routes/sitemap[.]xml.ts`
  - `src/server.ts`
  - `src/start.ts`
  - `src/styles.css`
