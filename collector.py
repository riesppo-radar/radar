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
MAX_PAGES = 2
TIMEOUT = 20
RETRIES = 3

# Modalidades mais relevantes para a Riesppo.
MODALITIES = {
    4: "Concorrência",
    6: "Pregão Eletrônico",
    8: "Dispensa",
    9: "Inexigibilidade",
    12: "Credenciamento",
}

LEGAL_KEYWORDS = {
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
        "consultoria legal",
    ],
    "Tributário": [
        "direito tributário",
        "direito tributario",
        "consultoria tributária",
        "consultoria tributaria",
        "assessoria tributária",
        "assessoria tributaria",
        "créditos tributários",
        "creditos tributarios",
        "recuperação de créditos tributários",
        "recuperacao de creditos tributarios",
        "execução fiscal",
        "execucao fiscal",
        "arrecadação tributária",
        "arrecadacao tributaria",
    ],
    "Administrativo / Contratos": [
        "direito administrativo",
        "assessoria em licitações",
        "assessoria em licitacoes",
        "consultoria em licitações",
        "consultoria em licitacoes",
        "licitações e contratos",
        "licitacoes e contratos",
        "contratos administrativos",
        "contratação pública",
        "contratacao publica",
    ],
    "RPPS / Previdenciário": [
        "rpps",
        "regime próprio de previdência",
        "regime proprio de previdencia",
        "direito previdenciário",
        "direito previdenciario",
        "assessoria previdenciária",
        "assessoria previdenciaria",
        "consultoria previdenciária",
        "consultoria previdenciaria",
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

# Expressões que normalmente indicam uma compra sem interesse
# para o Radar jurídico.
NEGATIVE_KEYWORDS = [
    "material de limpeza",
    "material de expediente",
    "medicamento",
    "medicamentos",
    "celular",
    "telefone celular",
    "computador",
    "notebook",
    "mobiliário",
    "mobiliario",
    "alimentos",
    "alimentação",
    "alimentacao",
    "obra",
    "obras",
    "construção",
    "construcao",
    "engenharia",
    "manutenção predial",
    "manutencao predial",
    "combustível",
    "combustivel",
    "veículo",
    "veiculo",
    "uniforme",
]


def norm(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "").lower(),
    ).strip()


def classify(title, description):
    text = norm(f"{title} {description}")

    areas = []

    for area, terms in LEGAL_KEYWORDS.items():
        if any(term in text for term in terms):
            areas.append(area)

    return areas


def is_relevant(title, description):
    text = norm(f"{title} {description}")

    areas = classify(title, description)

    if not areas:
        return False, []

    explicit_legal = any(
        term in text
        for term in LEGAL_KEYWORDS["Advocacia / Jurídico"]
    )

    # Se houver referência expressa a advocacia/jurídico,
    # entra mesmo que outras palavras apareçam.
    if explicit_legal:
        return True, areas

    # Evita falsos positivos de compras comuns.
    if any(
        term in text
        for term in NEGATIVE_KEYWORDS
    ):
        return False, areas

    return True, areas


def score(title, description, modality, areas):
    text = norm(f"{title} {description}")

    value = 20

    value += min(
        len(areas) * 10,
        40,
    )

    if any(
        term in text
        for term in [
            "escritório de advocacia",
            "serviços jurídicos",
            "serviços advocatícios",
            "contratação de advogado",
        ]
    ):
        value += 30

    if modality in {
        "Inexigibilidade",
        "Dispensa",
        "Credenciamento",
    }:
        value += 10

    return min(value, 100)


def request_json(url, params):
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; Riesppo-Radar/1.0)"
        ),
    }

    for attempt in range(1, RETRIES + 1):
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
                wait = attempt * 5
                print(
                    f"Rate limit. "
                    f"Aguardando {wait}s."
                )
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                wait = attempt * 3
                print(
                    f"Erro do PNCP. "
                    f"Aguardando {wait}s."
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            print(
                f"Falha HTTP "
                f"{attempt}/{RETRIES}: {exc}"
            )

            if attempt < RETRIES:
                time.sleep(attempt * 2)

    return None


def rows_from_payload(payload):
    if payload is None:
        return []

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        return (
            payload.get("data")
            or payload.get("content")
            or payload.get("items")
            or []
        )

    return []


def fetch_open_proposals(modality_id, modality_name, uf, end_date):
    endpoint = (
        f"{BASE}/v1/contratacoes/proposta"
    )

    for page in range(1, MAX_PAGES + 1):
        params = {
            "dataFinal": end_date,
            "codigoModalidadeContratacao": modality_id,
            "uf": uf,
            "pagina": page,
            "tamanhoPagina": PAGE_SIZE,
        }

        payload = request_json(
            endpoint,
            params,
        )

        if payload is None:
            print(
                f"Sem resposta: "
                f"{modality_name}/{uf}/página {page}"
            )
            break

        rows = rows_from_payload(payload)

        if not rows:
            break

        for row in rows:
            yield row, modality_name

        if len(rows) < PAGE_SIZE:
            break

        time.sleep(0.5)


def fetch_recent_publications(
    modality_id,
    modality_name,
    uf,
    start_date,
    end_date,
):
    endpoint = (
        f"{BASE}/v1/contratacoes/publicacao"
    )

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
            params,
        )

        if payload is None:
            break

        rows = rows_from_payload(payload)

        if not rows:
            break

        for row in rows:
            yield row, modality_name

        if len(rows) < PAGE_SIZE:
            break

        time.sleep(0.5)


def make_item(row, modality_name):
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

    relevant, areas = is_relevant(
        title,
        description,
    )

    if not relevant:
        return None

    org = row.get("orgaoEntidade") or {}
    unit = row.get("unidadeOrgao") or {}

    control = (
        row.get("numeroControlePNCP")
        or f"{row.get('cnpjOrgao', '')}-"
        f"{row.get('anoCompra', '')}-"
        f"{row.get('sequencialCompra', '')}"
    )

    source = (
        row.get("linkSistemaOrigem")
        or row.get("linkProcesso")
        or "https://pncp.gov.br/"
    )

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
        return json.loads(
            DATA.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
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

        parsed = datetime.fromisoformat(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed

    except (ValueError, TypeError):
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
        for item in existing.get(
            "opportunities",
            [],
        )
        if item.get("id")
    }

    print("=" * 70)
    print("RIESPPO RADAR")
    print(f"Publicações: {start} → {end}")
    print(
        "Estados: "
        + ", ".join(UFS)
    )
    print("=" * 70)

    # 1. Primeiro: aquilo que ESTÁ ABERTO para propostas.
    # Isso recebe prioridade máxima.
    for modality_id, modality_name in MODALITIES.items():
        for uf in UFS:
            print(
                f"[ABERTAS] "
                f"{modality_name} / {uf}"
            )

            for raw, modality in (
                fetch_open_proposals(
                    modality_id,
                    modality_name,
                    uf,
                    end,
                )
                or []
            ):
                item = make_item(
                    raw,
                    modality,
                )

                if not item:
                    continue

                existing_item = by_id.get(
                    item["id"]
                )

                item["first_seen_at"] = (
                    existing_item.get(
                        "first_seen_at"
                    )
                    if existing_item
                    else now.isoformat()
                )

                item["last_seen_at"] = (
                    now.isoformat()
                )

                item["status"] = "OPEN"

                by_id[item["id"]] = item

    # 2. Depois: publicações recentes.
    # Serve para descobrir novas oportunidades que
    # ainda precisam ser verificadas.
    for modality_id, modality_name in MODALITIES.items():
        for uf in UFS:
            print(
                f"[PUBLICADAS] "
                f"{modality_name} / {uf}"
            )

            for raw, modality in (
                fetch_recent_publications(
                    modality_id,
                    modality_name,
                    uf,
                    start,
                    end,
                )
                or []
            ):
                item = make_item(
                    raw,
                    modality,
                )

                if not item:
                    continue

                existing_item = by_id.get(
                    item["id"]
                )

                first_seen = (
                    existing_item.get(
                        "first_seen_at"
                    )
                    if existing_item
                    else now.isoformat()
                )

                item["first_seen_at"] = first_seen
                item["last_seen_at"] = (
                    now.isoformat()
                )

                # Se já identificamos como aberta,
                # NÃO sobrescreve esse estado.
                if existing_item and (
                    existing_item.get("status")
                    == "OPEN"
                ):
                    item["status"] = "OPEN"
                else:
                    item["status"] = "VERIFY"

                by_id[item["id"]] = item

    rows = list(by_id.values())

    # Tudo que apareceu como aberto nesta execução
    # permanece OPEN. O restante publicado recentemente
    # fica VERIFY, evitando a mentira de chamar tudo de aberto.
    rows.sort(
        key=lambda item: (
            item.get("status")
            not in ("OPEN", "VERIFY"),
            -(item.get("score") or 0),
            item.get("deadline_date") or "",
        )
    )

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

    open_count = sum(
        item.get("status") == "OPEN"
        for item in rows
    )

    verify_count = sum(
        item.get("status") == "VERIFY"
        for item in rows
    )

    print("=" * 70)
    print("COLETA CONCLUÍDA")
    print(f"Total: {len(rows)}")
    print(f"ABERTAS: {open_count}")
    print(f"VERIFICAR: {verify_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
