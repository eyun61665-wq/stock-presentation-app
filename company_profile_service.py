"""企業公式ページから会社概要の入力候補を無料で作る。"""
from __future__ import annotations

import re
from html import unescape
from typing import Any

import requests

from data_sources import USER_AGENT, cache_path


def _meta_content(html: str, key: str) -> str:
    patterns = (
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(key)}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return " ".join(unescape(match.group(1)).split())
    return ""


def profile_candidates(company_name: str, sector: str, description: str, source_url: str) -> dict[str, str]:
    """公式説明と業種から、上書き前提ではない検討候補を組み立てる。"""
    context = f"{company_name} {sector} {description}".lower()
    if any(word in context for word in ("セキュリティ", "security", "サイバー")):
        fallback_description = (
            f"確認候補：{company_name}は、サイバーセキュリティに関する教育・人材育成・"
            "コンサルティング等を展開する企業です。事業構成は最新の公式資料で確認してください。"
        )
        strength = "検討候補：専門人材・教育・コンサルティングを組み合わせ、顧客のセキュリティ課題に継続対応できる点。"
        thesis = "検討仮説：サイバー対策の高度化、ガイドライン対応、人材不足を背景に、教育・コンサル・運用支援の需要拡大を取り込めるか。"
        catalysts = "候補：セキュリティ関連制度・ガイドラインの改定、AIを使った攻撃と防御の高度化、人材育成需要、新サービス・提携。"
        risks = "主な確認点：技術変化への対応、人材採用・育成、競争激化、案件検収時期、情報管理・サービス品質。"
    elif any(word in context for word in ("半導体", "電子", "光学", "製造", "機械")):
        fallback_description = (
            f"確認候補：{company_name}は、{sector or '製造業'}に属する製品・技術サービスを展開する企業です。"
            "製品別の構成は最新の公式資料で確認してください。"
        )
        strength = "検討候補：固有技術、製品開発力、顧客基盤、生産・品質管理の積み上げ。"
        thesis = "検討仮説：技術世代交代や設備投資の回復局面で、高付加価値品の数量・単価が伸びるか。"
        catalysts = "候補：次世代技術への移行、設備投資、用途拡大、新製品、供給制約の解消、規格変更。"
        risks = "主な確認点：需要循環、在庫調整、価格競争、原材料・為替、顧客集中、開発や量産の遅延。"
    else:
        fallback_description = (
            f"確認候補：{company_name}は、{sector or '対象市場'}を中心に事業を展開する企業です。"
            "具体的な事業構成は最新の公式資料で確認してください。"
        )
        strength = f"検討候補：{sector or '主力領域'}での顧客基盤、商品・サービス、業務ノウハウ。"
        thesis = f"検討仮説：{sector or '対象市場'}の需要変化を、数量・単価・シェアの改善として取り込めるか。"
        catalysts = "候補：市場拡大、技術進化、法令・制度・業界ルールの変更、新商品、提携、価格改定。"
        risks = "主な確認点：競争、需要変動、コスト上昇、人材、規制変更、計画の実行遅延。"
    source = f"\n出典：企業公式サイト {source_url}" if source_url else ""
    return {
        "business_description": description or fallback_description,
        "strengths": strength + source,
        "investment_thesis": thesis,
        "catalysts": catalysts,
        "risks": risks,
    }


def fetch_company_profile(
    ir_url: str, company_name: str, sector: str = "", *, session=requests, timeout: int = 20,
) -> dict[str, str]:
    if not ir_url:
        # 業種コードだけから会社固有の説明を作ると誤情報になるため、根拠がなければ空欄にする。
        return {
            "business_description": "", "strengths": "", "investment_thesis": "",
            "catalysts": "", "risks": "",
        }
    cached = cache_path(ir_url, ".html")
    if cached.exists() and session is requests:
        html = cached.read_text(encoding="utf-8")
    else:
        response = session.get(ir_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
        raw = getattr(response, "content", b"")
        html = raw.decode("utf-8", errors="replace") if raw else response.text
    description = _meta_content(html, "description") or _meta_content(html, "og:description")
    if not description:
        return {
            "business_description": "", "strengths": "", "investment_thesis": "",
            "catalysts": "", "risks": "",
        }
    return {
        "business_description": f"公式サイト記載：{description}\n出典：{ir_url}",
        "strengths": "", "investment_thesis": "", "catalysts": "", "risks": "",
    }


def fill_empty_profile(project: dict[str, Any], candidates: dict[str, str]) -> dict[str, Any]:
    """手入力済みの文章は保持し、空欄だけを候補で補う。"""
    updated = dict(project)
    for field, value in candidates.items():
        if not str(updated.get(field) or "").strip() and value:
            updated[field] = value
    return updated
