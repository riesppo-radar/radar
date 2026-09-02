import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://pncp.gov.br/api/consulta"
DATA = Path("data/opportunities.json")

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))
UFS = [
    x.strip().upper()
    for x in os.getenv("UFS", "MG,BA,ES").split(",")
    if x.strip()
]

PAGE_SIZE = 50
MAX_PAGES = int(os.getenv("MAX_PAGES", "5"))

# Códigos conhecidos das modalidades da Lei 14.133.
# Depois podemos ampliar sem mudar a arquitetura.
MODALITIES = [
    1,  # Concorrência
    2,  # Concurso
    3,  # Leilão
    4,  # Pregão
    5,  # Diálogo competitivo
    6,  # Dispensa
    7,  # Inexigibilidade
    8,  # Credenciamento
]

KEYWORDS = {
    "Advocacia / Jurídico": [
        "escritório de advocacia",
        "serviços advocatícios",
        "serviços jurídicos",
        "servico juridico",
        "assessoria jurídica",
        "assessoria juridica",
        "consultoria jurídica",
        "consultoria juridica",
        "advocacia",
        "assessoramento jurídico",
        "assessoramento juridico",
    ],
    "Tributário": [
        "tributário",
        "tributaria",
        "recuperação de créditos tributários",
        "créditos tributários",
        "execução fiscal",
        "arrecadação tributária",
        "recuperação tributária",
    ],
    "Administrativo / Contratos": [
        "licitações e contratos",
        "licitação e contratos",
        "contratos administrativos",
        "direito administrativo",
        "assessoria em licitações",
        "consultoria em licitações",
        "contratação pública",
    ],
    "RPPS / Previdenciário": [
        "rpps",
        "regime próprio de previdência",
        "previdenciário",
        "previdenciaria",
        "previdência municipal",
    ],
    "Legislativo": [
        "assessoria legislativa",
        "consultoria legislativa",
        "processo legislativo",
        "consultoria jurídica legislativa",
    ],
    "Trabalhista": [
        "assessoria trabalhista",
        "consultoria trabalhista",
        "serviços trabalhistas",
        "direito do trabalho",
    ],
}

NEGATIVE = [
    "obra",
    "construção",
    "engenharia",
    "software",
    "informática",
    "publicidade",
    "marketing",
    "limpeza",
    "vigilância",
    "segurança",
    "manutenção",
    "alimentação",
    "medicamentos",
    "material hospitalar",
]


def norm(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def classify(text):
    text = norm(text)

    areas = []
    for area, words in KEYWORDS.items():
        if any(word in text for word in words):
            areas.append(area)

    return areas


def score(title, description, instrument, areas):
    text = norm(f"{title} {description}")

    value = 15
    value += min(len(areas) * 12, 48)

    if "escritório de advocacia" in text:
        value += 25

    if "serviços jurídicos" in text or "serviços advocatícios" in text:
        value += 25

    if any(
        x in norm(instrument)
        for x in ["inexigibilidade", "dispensa", "credenciamento"]
    ):
        value += 10

    return min(value, 100)


def request_json(url, params=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "Riesppo-Radar/1.0",
    }

    for attempt in range(5):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=45,
            )

            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            print(
                f"Falha HTTP: {url} | tentativa {attempt + 1}/5 | {exc}"
            )

            if attempt < 4:
                time.sleep(2 ** attempt)

    return None


def fetch_contracts(modality_id, uf, start_date, end_date):
    endpoint = f"{BASE}/v1/contratacoes/publicacao"

    for page in range(1, MAX_PAGES + 1):
        params = {
            "dataInicial": start_date,
            "dataFinal": end_date,
            "codigoModalidadeContratacao": modality_id,
            "uf": uf,
            "pagina": page,
            "tamanhoPagina": PAGE_SIZE,
        }

        payload = request_json(endpoint, params)

        if not payload:
            continue

        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("content") or []
        else:
            rows = payload

        if not rows:
            return

        for row in rows:
            yield row

        if len(rows) < PAGE_SIZE:
            return


def transform(row, modality_id):
    title = row.get("objetoCompra") or ""
    description = row.get("informacaoComplementar") or ""

    text = f"{title} {description}"
    areas = classify(text)

    if not areas:
        return None

    normalized = norm(text)
    explicit_legal = any(
        term in normalized for term in KEYWORDS["Advocacia / Jurídico"]
    )

    if not explicit_legal:
        if any(term in normalized for term in NEGATIVE) and len(areas) < 2:
            return None

    org = row.get("orgaoEntidade") or {}
    unit = row.get("unidadeOrgao") or {}

    publication = (
        row.get("dataPublicacaoPncp")
        or row.get("dataInclusao")
    )

    deadline = (
        row.get("dataEncerramentoProposta")
        or row.get("dataAberturaProposta")
    )

    source = (
        row.get("linkSistemaOrigem")
        or row.get("linkProcesso")
        or "https://pncp.gov.br/"
    )

    control = row.get("numeroControlePNCP")

    if not control:
        control = (
            f"{row.get('cnpjOrgao', '')}-"
            f"{row.get('anoCompra', '')}-"
            f"{row.get('sequencialCompra', '')}"
        )

    modality_name = row.get("modalidadeNome") or f"Modalidade {modality_id}"

    return {
        "id": f"PNCP:{control}",
        "source": "PNCP",
        "entity_type": "PUBLIC_ORG",
        "entity_name": (
            org.get("razaoSocial")
            or row.get("nomeOrgao")
            or "Órgão público"
        ),
        "cnpj": org.get("cnpj") or row.get("cnpjOrgao"),
        "uf": unit.get("ufSigla") or row.get("uf"),
        "city": (
            unit.get("municipioNome")
            or row.get("municipioNome")
        ),
        "demand_type": areas[0],
        "legal_area": " / ".join(areas),
        "instrument": modality_name,
        "title": title,
        "description": description,
        "source_url": source,
        "publication_date": publication,
        "deadline_date": deadline,
        "estimated_value": (
            row.get("valorTotalEstimado")
            or row.get("valorTotal")
        ),
        "score": score(
            title,
            description,
            modality_name,
            areas,
        ),
    }


def load_existing():
    if not DATA.exists():
        return {
            "last_scan": None,
            "opportunities": [],
        }

    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_scan": None,
            "opportunities": [],
        }


def parse_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except Exception:
        return None


def main():
    now = datetime.now(timezone.utc)

    start = (
        now - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y%m%d")

    end = now.strftime("%Y%m%d")

    existing = load_existing()

    by_id = {
        item["id"]: item
        for item in existing.get("opportunities", [])
        if item.get("id")
    }

    print(
        f"Riesppo Radar | período {start} → {end} | UFs={','.join(UFS)}"
    )

    for modality in MODALITIES:
        for uf in UFS:
            print(
                f"Consultando modalidade={modality} UF={uf}"
            )

            for raw in fetch_contracts(
                modality,
                uf,
                start,
                end,
            ) or []:

                item = transform(raw, modality)

                if not item:
                    continue

                previous = by_id.get(item["id"])

                item["first_seen_at"] = (
                    previous.get("first_seen_at")
                    if previous
                    else now.isoformat()
                )

                item["last_seen_at"] = now.isoformat()

                by_id[item["id"]] = item

                time.sleep(0.10)

    rows = []

    for item in by_id.values():
        deadline = parse_date(item.get("deadline_date"))

        if deadline and deadline < now:
            item["status"] = "CLOSED"
        else:
            first_seen = parse_date(item.get("first_seen_at"))

            if first_seen and (
                now - first_seen
            ).total_seconds() < 36 * 3600:
                item["status"] = "NEW"
            else:
                item["status"] = "OPEN"

        rows.append(item)

    rows.sort(
        key=lambda item: (
            item.get("status") not in ("NEW", "OPEN"),
            -(item.get("score") or 0),
            item.get("deadline_date") or "",
        )
    )

    rows = rows[:5000]

    DATA.parent.mkdir(parents=True, exist_ok=True)

    DATA.write_text(
        json.dumps(
            {
                "last_scan": now.isoformat(),
                "opportunities": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    new_count = sum(
        item.get("status") == "NEW"
        for item in rows
    )

    open_count = sum(
        item.get("status") == "OPEN"
        for item in rows
    )

    closed_count = sum(
        item.get("status") == "CLOSED"
        for item in rows
    )

    print(
        "COLETA CONCLUÍDA | "
        f"total={len(rows)} "
        f"novas={new_count} "
        f"abertas={open_count} "
        f"fechadas={closed_count}"
    )


if __name__ == "__main__":
    main()
