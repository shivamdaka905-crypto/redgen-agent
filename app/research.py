"""Lead generation + research: OpenStreetMap places, parallel enrichment, PDF output."""
import asyncio
import csv
import json
import re
import time
from urllib.parse import quote

import httpx

from . import store

UA = "RedGenAgent/1.0 (redgendev@gmail.com)"
NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = "https://overpass-api.de/api/interpreter"
MEDIA = store.WORKSPACE / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
PHONE = re.compile(r"\+977[\s\-]?\d{7,10}|\b9[678]\d{8}\b|\b0?1[\s\-]?\d{6,7}\b")
NICHE_TAGS = {
    "dentist": ['["amenity"="dentist"]', '["healthcare"="dentist"]', '["amenity"="clinic"]["healthcare"="dentist"]'],
    "dental": ['["amenity"="dentist"]', '["healthcare"="dentist"]'],
    "restaurant": ['["amenity"="restaurant"]'],
    "cafe": ['["amenity"="cafe"]'],
    "gym": ['["leisure"="fitness_centre"]'],
    "salon": ['["shop"="beauty"]', '["shop"="hairdresser"]'],
    "hotel": ['["tourism"="hotel"]'],
    "pharmacy": ['["amenity"="pharmacy"]'],
    "school": ['["amenity"="school"]'],
    "clinic": ['["amenity"="clinic"]'],
}


def _ev(kind, name, detail=""):
    try:
        store.add_event(store.CURRENT["conv"], kind, name, str(detail)[:1200])
    except Exception:
        pass


def _tags(niche):
    key = niche.lower().split()[0]
    return NICHE_TAGS.get(key, [f'["name"~"{niche}",i]'])


async def geocode(city):
    """City name -> bounding box for map queries."""
    _ev("query", f"geocode: {city}", "sent")
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": UA}) as client:
        resp = await client.get(f"{NOMINATIM}/search", params={
            "q": city, "format": "jsonv2", "limit": 1, "featuretype": "city"})
        data = resp.json()
    if not data:
        _ev("query", f"geocode: {city}", "not found")
        return None
    box = data[0].get("boundingbox")
    if not box:
        return None
    south, north, west, east = (float(x) for x in box)
    _ev("query", f"geocode: {city}", f"{south},{west} to {north},{east}")
    return {"south": south, "north": north, "west": west, "east": east,
            "label": data[0].get("display_name", city)[:80]}


async def places(city, niche="dental clinic", limit=60):
    """Dentists/clinics/etc in a city, straight from OpenStreetMap."""
    box = await geocode(city)
    if not box:
        return []
    bbox = f'{box["south"]},{box["west"]},{box["north"]},{box["east"]}'
    parts = [f'{sel}{tag}({bbox});' for tag in _tags(niche) for sel in ("node", "way")]
    query = f'[out:json][timeout:30];({";".join(p.rstrip(";") for p in parts)};);out center tags {limit};'
    _ev("worker", "map search", f'{niche} in {city}')
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        resp = await client.post(OVERPASS, data={"data": query})
        data = resp.json()
    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        rows.append({
            "name": name,
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "website": tags.get("website") or tags.get("contact:website") or "",
            "address": " ".join(filter(None, [tags.get("addr:street"), tags.get("addr:city")])),
        })
    _ev("worker", "map search", f"{len(rows)} places found in {city}")
    return rows


async def serper_lookup(name, city):
    """Optional: real web search for a phone number when SERPER_API_KEY is set."""
    import os
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post("https://google.serper.dev/search",
                                     headers={"X-API-KEY": key, "Content-Type": "application/json"},
                                     json={"q": f'{name} {city} phone number', "num": 5})
            data = resp.json()
    except Exception:
        return ""
    blob = json.dumps(data.get("organic", []) + [data.get("knowledgeGraph") or {}])
    found = PHONE.search(blob)
    return re.sub(r"[^\d+]", "", found.group(0)) if found else ""


async def enrich(rows, city, limit=20):
    """Fill missing phone numbers by looking each name up once (rate limited)."""
    gate = asyncio.Semaphore(1)
    todo = [r for r in rows if not r["phone"]][:limit]

    async def one(row):
        async with gate:
            await asyncio.sleep(1.1)
            try:
                async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
                    resp = await client.get(f"{NOMINATIM}/search", params={
                        "q": f'{row["name"]}, {city}', "format": "jsonv2",
                        "extratags": 1, "limit": 1})
                    data = resp.json()
            except Exception:
                return
        if not data:
            return
        extra = data[0].get("extratags") or {}
        phone = extra.get("phone") or extra.get("contact:phone") or ""
        if not phone:
            found = PHONE.search(json.dumps(extra))
            phone = found.group(0) if found else ""
        if not phone:
            phone = await serper_lookup(row["name"], city)
        if phone:
            row["phone"] = re.sub(r"[^\d+]", "", phone)
            _ev("worker", "phone lookup", f'{row["name"][:35]} -> {row["phone"]}')
    if todo:
        _ev("worker", "phone lookup", f"{len(todo)} places to enrich")
        await asyncio.gather(*[one(r) for r in todo])
    return rows


async def make_pdf(title, lines=None, rows=None, name="", columns=None):
    """Clean A4 PDF: title, notes, and a table."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    lines, rows = lines or [], rows or []
    columns = columns or ["Name", "Phone", "Website", "Area"]
    fname = name or f"report-{int(time.time())}.pdf"
    doc = SimpleDocTemplate(str(MEDIA / fname), pagesize=A4, topMargin=18 * mm,
                            bottomMargin=16 * mm, leftMargin=14 * mm, rightMargin=14 * mm)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 8)]
    for line in lines:
        story.append(Paragraph(str(line), styles["Normal"]))
    story.append(Spacer(1, 10))
    if rows:
        data = [[Paragraph(f"<b>{c}</b>", styles["BodyText"]) for c in columns]]
        keys = [c.lower().replace("area", "address") for c in columns]
        for row in rows:
            data.append([Paragraph(str(row.get(k, ""))[:70], styles["BodyText"]) for k in keys])
        widths = [72 * mm, 30 * mm, 45 * mm, 35 * mm]
        table = Table(data, colWidths=widths[:len(columns)], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
    doc.build(story)
    return f"/api/media/{fname}"


async def lead_finder(city, niche="dental clinic", max_results=20, no_website_only=True):
    """Build a lead list for a city: name, phone, website, area + PDF and CSV."""
    stamp = int(time.time())
    rows = await places(city, niche, 80)
    if no_website_only:
        targets = [r for r in rows if not r["website"]]
        label = "without a website"
    else:
        targets = rows
        label = "all"
    targets = targets[:max_results]
    _ev("worker", "lead_finder", f"{len(targets)} {label} targets, filling phones")
    await enrich(targets, city, limit=18)
    with_phone = [r for r in targets if r["phone"]]
    columns = ["Name", "Phone", "Website", "Area"]
    pdf = await make_pdf(f"{niche.title()} in {city} - {label}",
                         [f"Generated {time.strftime('%Y-%m-%d %H:%M')} by RedGen Agent",
                          f"{len(targets)} places scanned, {len(with_phone)} with a phone number.",
                          "Source: OpenStreetMap + Nominatim enrichment."],
                         rows=targets, name=f"leads-{stamp}.pdf", columns=columns)
    path = MEDIA / f"leads-{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns and ["name", "phone", "website", "address"])
        writer.writeheader()
        writer.writerows(targets)
    head = "\n".join(f"- {r['name'][:45]} | {r['phone'] or 'no phone'} | {r['website'] or 'no site'}"
                     for r in targets[:15])
    return (f"{niche.title()} in {city}: {len(targets)} {label}, {len(with_phone)} with phone\n"
            f"{head}\nPDF: {pdf}\nCSV: /api/media/{path.name}")


async def deep_research(topic, queries):
    """Several map/web angles at once, merged into one brief."""
    parts = [q.strip() for q in (queries or "").split("|") if q.strip()] or [topic]
    batches = await asyncio.gather(*[places(topic, part, 12) for part in parts])
    lines = []
    for part, batch in zip(parts, batches):
        lines.append(f"\n{part}: {len(batch)} places")
        for row in batch[:6]:
            lines.append(f"- {row['name']} | {row['phone'] or 'no phone'} | {row['website'] or 'no site'}")
    return f"Research: {topic}\n" + "\n".join(lines)


def _spec(name, desc, props, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []}}}


SCHEMAS = [
    _spec("lead_finder", "Find local businesses in a city with phone numbers and whether they have a "
          "website, then save a PDF + CSV list. Defaults to clinics WITHOUT a website (cold outreach targets).",
          {"city": {"type": "string"}, "niche": {"type": "string"}, "max_results": {"type": "integer"},
           "no_website_only": {"type": "boolean"}}, ["city"]),
    _spec("places", "List places of a type in a city from OpenStreetMap (name, phone, website, area).",
          {"city": {"type": "string"}, "niche": {"type": "string"}, "limit": {"type": "integer"}}, ["city"]),
    _spec("deep_research", "Research a topic from several map angles at once. Separate queries with |.",
          {"topic": {"type": "string"}, "queries": {"type": "string"}}, ["topic", "queries"]),
    _spec("make_pdf", "Save a clean A4 PDF report and return its download link.",
          {"title": {"type": "string"}, "lines": {"type": "array", "items": {"type": "string"}},
           "rows": {"type": "array", "items": {"type": "object"}}}, ["title"]),
]

HANDLERS = {"lead_finder": lead_finder, "places": places,
            "deep_research": deep_research, "make_pdf": make_pdf}
