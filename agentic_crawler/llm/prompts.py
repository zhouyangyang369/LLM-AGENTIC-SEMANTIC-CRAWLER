"""
Prompt テンプレート集 — すべての LLM 指示文はここで一元管理する。
"""


def prompt_filter_sitemap_urls(school: str, urls: list[str]) -> str:
    url_block = "\n".join(urls)
    return f"""あなたは日本の大学の入試情報収集の専門家です。

以下は「{school}」の公式サイトの URL 一覧（サイトマップから抽出）です。

【タスク】
入試・募集要項に関連する可能性がある URL を選別してください。

【選別基準】（以下のいずれかに該当すれば候補）
- 学部・大学院の入試、募集要項、選抜要項、出願に関するページ
- 研究科・学部・専攻の一覧ページ（そこからリンクが辿れるため）
- 入学案内・受験生向けページ

【含めるべきキーワード例】
入試, 募集, 要項, 出願, 入学, 選抜, 大学院, 研究科, 学部, 専攻,
admission, admissions, graduate, undergraduate, enrollment, apply

【除外すべきページ】
- ニュース・プレスリリース・イベント告知のみのページ
- 研究成果・論文・教員紹介のみのページ
- 図書館・施設・生協・食堂などのページ

URL 一覧:
{url_block}

以下の JSON 形式のみで回答してください:
{{
  "relevant_urls": ["url1", "url2", ...],
  "reason": "選別の簡単な根拠（1-2文）"
}}"""


def prompt_find_navigation_pages(school: str, urls: list[str]) -> str:
    url_block = "\n".join(urls)
    return f"""あなたは日本の大学サイト構造の専門家です。

「{school}」のサイトマップ URL 一覧から、
**研究科・学部・大学院の一覧ナビゲーションページ** を特定してください。

これらのページは「各研究科・学部へのリンク集」であり、
そこを起点に各研究科のサブサイト（別ドメイン含む）に辿り着けます。

URL 一覧:
{url_block}

以下の JSON 形式のみで回答してください:
{{
  "navigation_pages": [
    {{"url": "...", "type": "graduate_list|undergraduate_list|both", "note": "簡単な説明"}}
  ]
}}"""


def prompt_extract_subsites(school: str, page_markdown: str, base_url: str) -> str:
    return f"""あなたは日本の大学サイト構造の専門家です。

以下は「{school}」の大学院・学部一覧ページの内容です（Markdown 形式）。
ページ URL: {base_url}

【タスク】
各研究科・学部・学府・専攻への **入口リンク** を抽出してください。
特に以下に注目：
- 別サブドメインへのリンク（例: med.xxx.ac.jp, eng.xxx.ac.jp）
- 各研究科の独立サイトへのリンク
- 「大学院研究科一覧」「各学部サイト」等のリンク集

--- ページ内容 ---
{page_markdown[:8000]}
--- ここまで ---

以下の JSON 形式のみで回答してください:
{{
  "subsites": [
    {{
      "name": "研究科・学部名",
      "url": "入口URL",
      "is_different_domain": true/false,
      "category": "graduate|undergraduate|both|unknown"
    }}
  ]
}}"""


def prompt_extract_pdfs_from_page(school: str, page_url: str, page_markdown: str) -> str:
    return f"""あなたは日本の大学入試情報収集の専門家です。

以下は「{school}」の公式サイトのページ内容（Markdown 形式）です。
ページ URL: {page_url}

【タスク 1】このページに直接リンクされている PDF で、
**入試・募集関連** のものをすべて抽出してください。

対象となる PDF の例:
- 〇〇年度 募集要項
- 入学者選抜要項
- 出願書類一式
- 学生募集要項（学部・修士・博士・専門職）
- 推薦入試要項、外国人留学生選抜要項 等

【タスク 2】このページに、募集要項 PDF へ辿り着けそうな
**さらに深いリンク** があれば列挙してください。
例: 各研究科の入試ページ、「詳細はこちら」リンク等

--- ページ内容 ---
{page_markdown[:10000]}
--- ここまで ---

以下の JSON 形式のみで回答してください:
{{
  "pdfs": [
    {{
      "url": "https://...",
      "text": "リンクテキスト",
      "category": "学部|修士|博士|専門職|不明",
      "year": "2025 など判明すれば",
      "department": "研究科・学部名（判明すれば）",
      "admission_type": "一般|推薦|外国人|社会人|不明"
    }}
  ],
  "follow_links": [
    {{"url": "https://...", "reason": "なぜ辿るべきか"}}
  ],
  "has_more": true/false
}}"""


def prompt_audit_completeness(school: str, found_departments: list[str], found_pdfs: list[dict]) -> str:
    dept_block = "\n".join(f"- {d}" for d in found_departments) if found_departments else "（なし）"
    pdf_summary = "\n".join(
        f"- [{p.get('category','?')}] {p.get('department','?')}: {p.get('text','?')}"
        for p in found_pdfs[:30]
    )
    return f"""あなたは日本の大学院・大学の入試情報に詳しい専門家です。

「{school}」の募集要項 PDF の収集状況を評価してください。

【収集済み研究科・学部】
{dept_block}

【収集済み PDF（最大30件）】
{pdf_summary}

【タスク】
1. あなたの知識で「{school}」にある主要な研究科・学部をリストアップしてください
2. 上記で収集できていない研究科・学部はどれですか？
3. さらに検索すべき具体的なクエリを提案してください

以下の JSON 形式のみで回答してください:
{{
  "known_departments": ["研究科名1", "研究科名2", ...],
  "missing_departments": ["未収集の研究科名1", ...],
  "suggested_queries": [
    "{{school}} 〇〇研究科 募集要項 2025",
    ...
  ],
  "is_complete": true/false,
  "completeness_note": "評価コメント"
}}"""
