import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://pncp.gov.br/api/consulta"
DATA = Path("data/opportunities.json")

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "2"))
UFS = [
    x.strip().upper()
    for x in os.getenv("UFS", "MG,BA,ES").split(",")
    if x.strip()
]

# Para o Radar, começamos pelas modalidades que mais
# provavelmente contêm contratação relevante para serviços jurídicos.
MODALITIES = {
    4: "Concorrência",
    6: "Pregão Eletrônico",
    8: "Dispensa",
    9: "Inexigibilidade",
}

PAGE_SIZE = 50
MAX_PAGES = 2
TIMEOUT = 20
MAX_RETRIES = 3

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
        "parecer jurídico",
        "parecer juridico",
    ],
    "Tributário": [
        "tributário",
        "tributaria",
        "tributário municipal",
        "tributaria municipal",
        "recuperação de créditos tributários",
        "recuperacao de creditos tributarios",
        "créditos tributários",
        "creditos tributarios",
        "execução fiscal",
        "execucao fiscal",
        "arrecadação tributária",
        "arrecadacao tributaria",
        "recuperação tributária",
        "recuperacao tributaria",
    ],
    "Administrativo / Contratos": [
        "licitações e contratos",
        "licitacoes e contratos",
        "licitação e contratos",
        "licitacao e contratos",
        "contratos administrativos",
        "direito administrativo",
        "assessoria em licitações",
        "assessoria em licitacoes",
        "consultoria em licitações",
        "consultoria em licitacoes",
        "contratação pública",
        "contratacao publica",
    ],
    "RPPS / Previdenciário": [
        "rpps",
        "regime próprio de previdência",
        "regime proprio de previdencia",
        "previdenciário",
        "previdenciaria",
        "previdência municipal",
        "previdencia municipal",
    ],
    "Legislativo": [
        "assessoria legislativa",
        "consultoria legislativa",
        "processo legislativo",
        "consultoria jurídica legislativa",
        "consultoria juridica legislativa",
    ],
    "Trabalhista": [
        "assessoria trabalhista",
        "consultoria trabalhista",
        "serviços trabalhistas",
        "servicos trabalhistas",
        "direito do trabalho",
    ],
}

# Termos que geralmente indicam contratação sem relação
# com aquilo que queremos.
NEGATIVE = [
    "obra",
    "construção",
    "construcao",
    "engenharia",
    "software",
    "informática",
    "informatica",
    "publicidade",
    "marketing",
    "limpeza",
    "vigilância",
    "vigilancia",
    "segurança",
    "seguranca",
    "manutenção",
    "manutencao",
    "alimentação",
    "alimentacao",
    "medicamentos",
    "material hospitalar",
]


def norm(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def classify(text):
    text = norm(text)

    areas = []

    for area, terms in KEYWORDS.items():
        if any(term in text for term in terms):
            areas.append(area)

    return areas


def calculate_score(title, description, modality, areas):
    text = norm(f"{title} {description}")

    score = 10

    # Quanto mais áreas jurídicas detectadas, melhor.
    score += min(len(areas) * 10, 40)

    # Contratação expressamente jurídica.
    if "escritório de advocacia" in text:
        score += 30

    if "serviços jurídicos" in text or "serviços advocatícios" in text:
        score += 25

    # Inexigibilidade/dispensa/credenciamento possuem especial
    # relevância para nossa finalidade.
    if modality in {
        "Inexigibilidade",
        "Dispensa",
    }:
        score += 10

    return min(score, 100)


def request_json(url, params=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; Riesppo-Radar/1.0; +https://github.com/riesppo-radar/radar)"
        ),
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=TIMEOUT,
            )

            print(
                f"HTTP {response.status_code} | "
                f"{response.url}"
            )

            if response.status_code == 429:
                wait = 5 * attempt
                print(
                    f"Rate limit. Aguardando {wait}s..."
                )
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                wait = 3 * attempt
                print(
                    f"Erro do servidor. Aguardando {wait}s..."
                )
                time.sleep(wait)
                continue

            response.raise_for_status()

            return response.json()

        except requests.RequestException as exc:
            print(
                f"Falha na requisição "
                f"{attempt}/{MAX_RETRIES}: {exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

    return None


def fetch_contracts(
    modality_id,
    modality_name,
    uf,
    start_date,
    end_date,
):
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

        payload = request_json(
            endpoint,
            params=params,
        )

        if payload is None:
            print(
                f"Consulta falhou: "
                f"{modality_name} / {uf} / página {page}"
            )
            continue

        if isinstance(payload, dict):
            rows = (
                payload.get("data")
                or payload.get("content")
                or []
            )
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        if not rows:
            return

        for row in rows:
            yield row, modality_name

        if len(rows) < PAGE_SIZE:
            return

        # Pequena pausa para não martelar a API.
        time.sleep(0.5)


def transform(row, modality_name):
    title = (
        row.get("objetoCompra")
        or row.get("objeto")
        or ""
    )

    description = (
        row.get("informacaoComplementar")
        or row.get("informacao_complementar")
        or ""
    )

    text = f"{title} {description}"

    areas = classify(text)

    if not areas:
        return None

    normalized = norm(text)

    # Se só bateu um termo genérico de área e há forte sinal
    # de que é uma compra completamente alheia ao jurídico,
    # descartamos.
    if (
        not any(
            term in normalized
            for term in KEYWORDS["Advocacia / Jurídico"]
        )
        and any(term in normalized for term in NEGATIVE)
        and len(areas) < 2
    ):
        return None

    org = row.get("orgaoEntidade") or {}
    unit = row.get("unidadeOrgao") or {}

    publication = (
        row.get("dataPublicacaoPncp")
        or row.get("dataInclusao")
        or row.get("dataPublicacao")
    )

    deadline = (
        row.get("dataEncerramentoProposta")
        or row.get("dataFimRecebimentoProposta")
        or row.get("dataAberturaProposta")
    )

    source = (
        row.get("linkSistemaOrigem")
        or row.get("linkProcesso")
        or row.get("linkPncp")
        or "https://pncp.gov.br/"
    )

    control = row.get("numeroControlePNCP")

    if not control:
        control = (
            f"{row.get('cnpjOrgao', '')}-"
            f"{row.get('anoCompra', '')}-"
            f"{row.get('sequencialCompra', '')}"
        )

    return {
        "id": f"PNCP:{control}",
        "source": "PNCP",
        "entity_type": "PUBLIC_ORG",
        "entity_name": (
            org.get("razaoSocial")
            or row.get("nomeOrgao")
            or "Órgão público"
        ),
        "cnpj": (
            org.get("cnpj")
            or row.get("cnpjOrgao")
        ),
        "uf": (
            unit.get("ufSigla")
            or row.get("uf")
        ),
        "city": (
            unit.get("municipioNome")
            or row.get("municipioNome")
            or ""
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
        "score": calculate_score(
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
        return json.loads(
            DATA.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        print(
            "Arquivo existente inválido. "
            "Começando do zero."
        )

        return {
            "last_scan": None,
            "opportunities": [],
        }


def parse_date(value):
    if not value:
        return None

    try:
        value = str(value)

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        result = datetime.fromisoformat(value)

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )

        return result

    except (ValueError, TypeError):
        return None


def main():
    now = datetime.now(timezone.utc)

    start = (
        now - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y%m%d")

    end = now.strftime("%Y%m%d")

    print("=" * 70)
    print("RIESPPO RADAR")
    print(f"Período: {start} → {end}")
    print(f"UFs: {', '.join(UFS)}")
    print(
        "Modalidades: "
        + ", ".join(MODALITIES.values())
    )
    print("=" * 70)

    existing = load_existing()

    by_id = {
        item["id"]: item
        for item in existing.get(
            "opportunities",
            [],
        )
        if item.get("id")
    }

    collected = 0

    for modality_id, modality_name in MODALITIES.items():
        for uf in UFS:
            print(
                f"\n>>> {modality_name} | {uf}"
            )

            for raw, modality in fetch_contracts(
                modality_id,
                modality_name,
                uf,
                start,
                end,
            ) or []:

                item = transform(
                    raw,
                    modality,
                )

                if not item:
                    continue

                previous = by_id.get(item["id"])

                item["first_seen_at"] = (
                    previous.get("first_seen_at")
                    if previous
                    else now.isoformat()
                )

                item["last_seen_at"] = (
                    now.isoformat()
                )

                by_id[item["id"]] = item

                collected += 1

    rows = []

    for item in by_id.values():
        deadline = parse_date(
            item.get("deadline_date")
        )

        if deadline and deadline < now:
            item["status"] = "CLOSED"
        else:
            first_seen = parse_date(
                item.get("first_seen_at")
            )

            if (
                first_seen
                and (
                    now - first_seen
                ).total_seconds()
                < 36 * 3600
            ):
                item["status"] = "NEW"
            else:
                item["status"] = "OPEN"

        rows.append(item)

    rows.sort(
        key=lambda item: (
            item.get("status")
            not in ("NEW", "OPEN"),
            -(item.get("score") or 0),
            item.get("deadline_date") or "",
        )
    )

    # Limite de segurança.
    rows = rows[:5000]

    DATA.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    print("\n" + "=" * 70)
    print("COLETA CONCLUÍDA")
    print(f"Registros processados: {collected}")
    print(f"Total armazenado: {len(rows)}")
    print(f"NOVAS: {new_count}")
    print(f"ABERTAS: {open_count}")
    print(f"FECHADAS: {closed_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
