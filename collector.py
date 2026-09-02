import json, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

BASE='https://pncp.gov.br/api/pncp''
DATA=Path('data/opportunities.json')
DAYS=int(os.getenv('LOOKBACK_DAYS','2'))
UFS=[x.strip().upper() for x in os.getenv('UFS','MG,BA,ES').split(',') if x.strip()]
MAX_PAGES=int(os.getenv('MAX_PAGES','4'))
PAGE_SIZE=50

KEYWORDS={
 'Advocacia / Jurídico':['escritório de advocacia','serviços advocatícios','serviços jurídicos','servico juridico','assessoria jurídica','assessoria juridica','consultoria jurídica','consultoria juridica','advocacia'],
 'Tributário':['tributário','tributaria','tributário','recuperação de créditos tributários','créditos tributários','execução fiscal','arrecadação tributária'],
 'Administrativo / Contratos':['licitações e contratos','licitação e contratos','contratos administrativos','direito administrativo','assessoria em licitações','consultoria em licitações'],
 'RPPS / Previdenciário':['rpps','regime próprio de previdência','previdenciário','previdenciaria','previdência municipal'],
 'Legislativo':['assessoria legislativa','consultoria legislativa','processo legislativo','consultoria jurídica legislativa'],
 'Trabalhista':['assessoria trabalhista','consultoria trabalhista','serviços trabalhistas','direito do trabalho']}

# Terms that indicate a generic non-legal service even when "consultoria/assessoria" appears.
NEG=['obra','construção','engenharia','software','informática','publicidade','marketing','limpeza','vigilância','segurança','manutenção','alimentação','medicamentos','material hospitalar']

def norm(s):
    return re.sub(r'\s+',' ',str(s or '').lower()).strip()

def classify(text):
    t=norm(text)
    areas=[a for a,words in KEYWORDS.items() if any(w in t for w in words)]
    return areas

def calc_score(title, desc, instrument, areas):
    t=norm(f'{title} {desc}')
    s=15+min(len(areas)*12,48)
    if 'escritório de advocacia' in t or 'serviços jurídicos' in t or 'serviços advocatícios' in t: s+=25
    if any(x in norm(instrument) for x in ['inexigibilidade','dispensa','credenciamento']): s+=10
    if any(x in t for x in ['prazo','proposta','contratação','contratar']): s+=4
    return min(100,s)

def get_modalities():
    r=requests.get(f'{BASE}/v1/modalidades',params={'statusAtivo':'true'},timeout=30)
    r.raise_for_status(); j=r.json()
    if isinstance(j,dict): j=j.get('data',j.get('modalidades',[]))
    ids=[]
    for x in j or []:
        if x.get('statusAtivo',True): ids.append((x.get('id'),x.get('nome','')))
    return ids

def fetch(mod_id,uf,start,end):
    for page in range(1,MAX_PAGES+1):
        params={'dataInicial':start,'dataFinal':end,'codigoModalidadeContratacao':mod_id,'uf':uf,'pagina':page,'tamanhoPagina':PAGE_SIZE}
        for attempt in range(3):
            try:
                r=requests.get(f'{BASE}/v1/contratacoes/publicacao',params=params,timeout=45)
                if r.status_code in (429,500,502,503,504): time.sleep(2**attempt); continue
                r.raise_for_status();j=r.json();break
            except Exception:
                if attempt==2:return
                time.sleep(2**attempt)
        rows=(j.get('data') if isinstance(j,dict) else j) or []
        if not rows: return
        for x in rows: yield x
        if len(rows)<PAGE_SIZE:return

def transform(x, modality_name):
    title=x.get('objetoCompra') or ''
    desc=x.get('informacaoComplementar') or ''
    text=f'{title} {desc}'
    areas=classify(text)
    if not areas:return None
    # Reject obvious false positives unless explicit legal language was found.
    t=norm(text)
    explicit=any(k in t for k in KEYWORDS['Advocacia / Jurídico'])
    if not explicit and any(n in t for n in NEG) and len(areas)<2:return None
    unit=x.get('unidadeOrgao') or {}
    org=x.get('orgaoEntidade') or {}
    pub=x.get('dataPublicacaoPncp') or x.get('dataInclusao')
    deadline=x.get('dataEncerramentoProposta') or x.get('dataAberturaProposta')
    source=x.get('linkSistemaOrigem') or x.get('linkProcesso') or 'https://pncp.gov.br/'
    sid=x.get('numeroControlePNCP') or f"{x.get('cnpjOrgao','')}-{x.get('anoCompra','')}-{x.get('sequencialCompra','')}"
    return {'id':f'PNCP:{sid}','source':'PNCP','entity_type':'PUBLIC_ORG','entity_name':org.get('razaoSocial') or x.get('nomeOrgao') or 'Órgão público','cnpj':org.get('cnpj') or x.get('cnpjOrgao'),'uf':unit.get('ufSigla') or x.get('uf'), 'city':unit.get('municipioNome') or x.get('municipioNome'),'demand_type':areas[0], 'legal_area':' / '.join(areas),'instrument':x.get('modalidadeNome') or modality_name,'title':title,'description':desc,'source_url':source,'publication_date':pub,'deadline_date':deadline,'estimated_value':x.get('valorTotalEstimado') or x.get('valorTotal'), 'score':calc_score(title,desc,x.get('modalidadeNome') or modality_name,areas),'areas':areas}

def main():
    now=datetime.now(timezone.utc); start=(now-timedelta(days=DAYS)).strftime('%Y%m%d'); end=now.strftime('%Y%m%d')
    existing={'last_scan':None,'opportunities':[]}
    if DATA.exists():
        try:existing=json.loads(DATA.read_text(encoding='utf8'))
        except Exception:pass
    byid={x['id']:x for x in existing.get('opportunities',[]) if x.get('id')}
    modalities=get_modalities()
    for mid,mname in modalities:
        if not mid:continue
        for uf in UFS:
            for raw in fetch(mid,uf,start,end) or []:
                item=transform(raw,mname)
                if not item:continue
                old=byid.get(item['id'])
                item['first_seen_at']=old.get('first_seen_at') if old else now.isoformat()
                item['last_seen_at']=now.isoformat()
                byid[item['id']]=item
            time.sleep(0.15)
    # Status by deadline; NEW remains new on the day it was first captured.
    for x in byid.values():
        d=x.get('deadline_date')
        try: closed=d and datetime.fromisoformat(d.replace('Z','+00:00')) < now
        except Exception: closed=False
        if closed: x['status']='CLOSED'
        else:
            try: first=datetime.fromisoformat(x['first_seen_at'].replace('Z','+00:00'))
            except Exception:first=now
            x['status']='NEW' if (now-first).total_seconds()<36*3600 else 'OPEN'
    rows=sorted(byid.values(),key=lambda a:(a.get('status') not in ('NEW','OPEN'),-(a.get('score') or 0),a.get('deadline_date') or ''))
    rows=rows[:5000]
    DATA.write_text(json.dumps({'last_scan':now.isoformat(),'opportunities':rows},ensure_ascii=False,indent=2),encoding='utf8')
    print(f'Coleta concluída: {len(rows)} oportunidades armazenadas; novas={sum(x.get("status")=="NEW" for x in rows)}')

if __name__=='__main__': main()
