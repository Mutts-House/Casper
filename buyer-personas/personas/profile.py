"""Turn segmented customers into persona cards with provenance tags.

Every attribute on a card is tagged:
  observed  - measured from transactions (highest confidence)
  stated    - self-reported in a survey (only shown when the group is big enough)
  inferred  - playbook assumption about why a segment behaves this way
"""

from __future__ import annotations

import re

import pandas as pd

from .config import PLAYBOOK
from .plugins import AIProvider

MIN_GROUP_FOR_STATED = 5  # survey answers from fewer people are not shown
DAYPART_NICE = {
    "commute_am": "weekday morning commute",
    "commute_pm": "evening commute",
    "late_night": "late night",
    "midday": "midday",
    "afternoon": "afternoon",
    "other": "mixed hours",
}
CATEGORY_NICE = {
    "fuel": "fuel",
    "food_service": "food service",
    "beverage": "beverages",
    "snack": "snacks",
    "other": "general merchandise",
    "restricted": "age-restricted items",
}


def _stated_profile(group_keys: pd.Series, survey: pd.DataFrame | None) -> dict:
    if survey is None or survey.empty:
        return {}
    answers = survey[survey["customer_key"].isin(group_keys)]
    if len(answers) < MIN_GROUP_FOR_STATED:
        return {"_note": f"{len(answers)} survey responses (below {MIN_GROUP_FOR_STATED}, hidden)"} if len(answers) else {}
    out = {}
    for col in answers.columns:
        if col == "customer_key":
            continue
        vals = answers[col].dropna().astype(str)
        if vals.empty:
            continue
        top = vals.value_counts()
        out[col] = {"top": top.index[0], "share": round(top.iloc[0] / len(vals), 2), "n": int(len(vals))}
    return out


def build_personas(
    seg_feats: pd.DataFrame,
    survey: pd.DataFrame | None,
    trend_hooks: dict[str, list[dict]],
    identified_revenue: float,
) -> list[dict]:
    if seg_feats.empty:
        return []
    personas = []
    grouped = seg_feats.groupby("segment")
    # Rank by gross profit, not revenue: fuel dollars would otherwise dominate.
    total_gp = float(seg_feats["gross_profit"].sum()) or 1.0
    ordered = grouped["gross_profit"].sum().sort_values(ascending=False).index
    for i, seg in enumerate(ordered, start=1):
        g = grouped.get_group(seg)
        pb = PLAYBOOK.get(seg, {})
        top_cat = g["top_category"].mode().iloc[0] if not g["top_category"].empty else "other"
        peak = g["peak_daypart"].mode().iloc[0] if not g["peak_daypart"].empty else "other"
        personas.append({
            "id": f"P{i}",
            "segment": seg,
            "summary": pb.get("summary", ""),
            "size": {
                "customers": int(len(g)),
                "share_of_identified_revenue_pct": round(g["spend"].sum() / identified_revenue * 100, 1) if identified_revenue else 0,
                "gross_profit_usd": round(float(g["gross_profit"].sum()), 2),
                "share_of_gross_profit_pct": round(float(g["gross_profit"].sum()) / total_gp * 100, 1),
                "provenance": "observed",
            },
            "behavior": {
                "avg_visits_in_window": round(g["visits"].mean(), 1),
                "visits_per_month": round(g["visits_per_month"].mean(), 2),
                "avg_basket_usd": round(g["avg_basket"].mean(), 2),
                "median_days_since_last_visit": int(g["recency_days"].median()),
                "peak_daypart": DAYPART_NICE.get(peak, peak),
                "top_category": CATEGORY_NICE.get(top_cat, top_cat),
                "weekend_share_pct": round(g["share_weekend"].mean() * 100, 1),
                "provenance": "observed",
            },
            "stated": _stated_profile(g["customer_key"], survey),
            "playbook": {
                "goal": pb.get("goal", ""),
                "offer_angle": pb.get("offer_angle", ""),
                "channel": pb.get("channel", ""),
                "timing": pb.get("timing", ""),
                "avoid": pb.get("avoid", ""),
                "provenance": "inferred",
            },
            "trend_hooks": trend_hooks.get(seg, [])[:5],
            "narrative": "",
            "narrative_source": "",
        })
    return personas


def facts_sheet(personas: list[dict], store_name: str, period: str) -> dict:
    """The ONLY thing sent to an AI provider: aggregate facts, no identifiers."""
    return {
        "store": store_name,
        "period": period,
        "personas": [
            {
                "name": p["segment"],
                "share_of_revenue_pct": p["size"]["share_of_identified_revenue_pct"],
                "share_of_gross_profit_pct": p["size"].get("share_of_gross_profit_pct"),
                "customers": p["size"]["customers"],
                "behavior": {k: v for k, v in p["behavior"].items() if k != "provenance"},
                "playbook": {k: v for k, v in p["playbook"].items() if k != "provenance"},
                "current_trend_topics": [t["topic"] for t in p.get("trend_hooks", [])],
            }
            for p in personas
        ],
    }


SYSTEM_PROMPT = (
    "You write concise marketing persona narratives for a convenience store owner. "
    "Use only the facts provided. Do not invent demographics, names, or statistics. "
    "Do not reference age-restricted products (tobacco, alcohol, lottery) in offers. "
    "Do not introduce any number that does not appear in the facts. "
    "Keep it under 120 words, in plain direct English, and end with one practical next step."
)


def template_narrative(p: dict) -> str:
    b = p["behavior"]
    return (
        f"{p['segment']} make up {p['size']['share_of_identified_revenue_pct']}% of identified revenue "
        f"({p['size']['customers']} customers). They average {b['avg_visits_in_window']} visits in the window, "
        f"with a ${b['avg_basket_usd']} average basket, and most often shop during {b['peak_daypart']}. "
        f"Goal: {p['playbook']['goal']} Suggested offer: {p['playbook']['offer_angle']} "
        f"Channel: {p['playbook']['channel']}, timing: {p['playbook']['timing']}."
    )


_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers_in_text(text: str) -> list[tuple[float, int]]:
    """Return (value, decimals) for every number written in a string."""
    found = []
    for token in _NUMBER_RE.findall(text):
        clean = token.replace(",", "")
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        found.append((float(clean), decimals))
    return found


def _numbers_in_facts(obj) -> list[float]:
    """Every number the facts sheet contains, including numbers inside strings."""
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out += [v for v, _ in _numbers_in_text(obj)]
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _numbers_in_facts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _numbers_in_facts(v)
    return out


def ungrounded_figures(text: str, persona_facts: dict) -> list[str]:
    """Return every figure in `text` that is not supported by `persona_facts`.

    A figure is grounded when some number in the facts, rounded to the same
    number of decimals the narrative uses, equals it. So "17.9" is grounded by
    17.93 (rounds to 17.9) and "18" is grounded by 17.93 (rounds to 18). An
    invented figure such as "42%" that appears nowhere in the facts is flagged.
    """
    facts_numbers = _numbers_in_facts(persona_facts)
    bad = []
    for value, decimals in _numbers_in_text(text):
        grounded = any(round(f, decimals) == round(value, decimals) for f in facts_numbers)
        if not grounded:
            shown = f"{value:.{decimals}f}"
            bad.append(shown)
    return bad


def add_narratives(personas: list[dict], facts: dict, provider: AIProvider) -> list[dict]:
    """Fill in each persona's narrative.

    AI text is used only if every figure in it is grounded in that persona's
    facts sheet. Otherwise the deterministic template is kept and the rejected
    figures are recorded in narrative_source. Errors also fall back to the
    template. Returns a log of rejections for the run summary.
    """
    by_name = {f["name"]: f for f in facts["personas"]}
    rejections: list[dict] = []
    for p in personas:
        p["narrative"] = template_narrative(p)
        p["narrative_source"] = "template"
        if provider.name == "template":
            continue
        persona_facts = by_name[p["segment"]]
        prompt = (
            "Facts for one customer persona (aggregate only):\n"
            f"{persona_facts}\n\nWrite the persona narrative. Use only figures that appear above."
        )
        try:
            text = (provider.generate(SYSTEM_PROMPT, prompt) or "").strip()
        except Exception as exc:  # keep the pipeline running; record why
            p["narrative_source"] = f"template (AI failed: {exc})"
            continue
        if not text:
            p["narrative_source"] = "template (AI returned empty text)"
            continue
        bad = ungrounded_figures(text, persona_facts)
        if bad:
            p["narrative_source"] = f"template (AI rejected: ungrounded figures {', '.join(bad)})"
            rejections.append({"persona": p["segment"], "figures": bad})
            continue
        p["narrative"] = text
        p["narrative_source"] = provider.name
    return rejections
