# 环境变量配置说明

> ⚠️ 由于工具安全策略限制，无法直接为你生成 `.env` / `.env.example` 文件。
> 请按照下面的步骤**手动创建** `.env` 文件。

---

## 1. 创建 `.env` 文件

在项目根目录（与 `run_phase3.py` 同级）创建一个名为 `.env` 的文本文件。

### PowerShell 一键创建（推荐）

```powershell
@"
# ============================================================
# Supabase
# 在 Supabase 控制台 → Settings → API 获取
# ============================================================
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxxxxxxxxxxxxx

# ============================================================
# LLM (OpenAI)
# ============================================================
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 可选：自定义 API base
# OPENAI_API_BASE=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o-mini

# ============================================================
# Tavily 搜索 API（用于第三阶段 crawl）
# 在 https://tavily.com/ 注册获取
# ============================================================
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ============================================================
# 可选
# ============================================================
# LOG_LEVEL=INFO
"@ | Out-File -FilePath .env -Encoding utf8 -NoNewline
```

执行后，把文件中的 `xxxxx` 占位符替换为你真实的密钥即可。

---

## 2. 字段说明

| 变量 | 必需 | 说明 | 获取方式 |
|---|---|---|---|
| `SUPABASE_URL` | ✅ | Supabase 项目 URL | Supabase 控制台 → Settings → API → "Project URL" |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | service_role 密钥（绕过 RLS） | Supabase 控制台 → Settings → API → "service_role" |
| `OPENAI_API_KEY` | crawl 阶段需要 | LLM 调用密钥 | https://platform.openai.com/api-keys |
| `TAVILY_API_KEY` | crawl 阶段需要 | 搜索 API | https://tavily.com/ |

> 仅运行 `import-excel`（Excel → DB）时，**只需要 Supabase 两个变量**即可。
> `OPENAI_API_KEY` 和 `TAVILY_API_KEY` 在后续 `crawl` 子命令时才会用到。

---

## 3. 安全提醒

- ❌ **绝对不要** 把 `.env` 提交到 git
- ✅ 确保项目根目录有 `.gitignore`，且包含 `.env` 一行
- ✅ `service_role` key 拥有数据库完全权限，泄露相当于数据库被黑

检查 `.gitignore`：

```powershell
Select-String -Path .gitignore -Pattern '^\.env$' -SimpleMatch
```

如果没有匹配输出，请追加：

```powershell
Add-Content -Path .gitignore -Value "`n.env"
```

---

## 4. 验证配置

创建好 `.env` 后，运行：

```powershell
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('SUPABASE_URL:', os.environ.get('SUPABASE_URL', 'NOT SET')[:30] + '...'); print('SUPABASE_SERVICE_ROLE_KEY:', 'SET' if os.environ.get('SUPABASE_SERVICE_ROLE_KEY') else 'NOT SET')"
```

如果输出形如：

```
SUPABASE_URL: https://abcdefg.supabase.co...
SUPABASE_SERVICE_ROLE_KEY: SET
```

说明配置成功，可以开始正式导入：

```powershell
python run_phase3.py import-excel --excel data/R06_daigaku.xlsx --dry-run   # 先试运行
python run_phase3.py import-excel --excel data/R06_daigaku.xlsx             # 正式导入
```

---

## 5. 安装依赖（如果还没装）

```powershell
pip install -r requirements_phase3.txt
```