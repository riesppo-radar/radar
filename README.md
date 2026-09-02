# RIESPPO RADAR

Plataforma automática para encontrar oportunidades de contratação da Riesppo.

## O que ela faz
- Varre o PNCP automaticamente a cada 2 horas.
- Procura demandas jurídicas no Executivo, Legislativo, autarquias, fundações, consórcios e demais órgãos que publiquem no PNCP.
- Separa `ABRIU`, `AINDA ABERTA` e `FECHOU`.
- Calcula prioridade.
- Mantém a fonte original para conferência.
- É preparada para receber depois portais de fornecedores privados (bancos, mineradoras, indústrias, concessionárias etc.).

## Custo
A versão pode ser publicada em um repositório público do GitHub com GitHub Pages + GitHub Actions. O GitHub informa que Actions em repositórios públicos são gratuitos; em conta Free, o limite é bloqueado quando a franquia é atingida se não houver método de pagamento, evitando cobrança automática. O coletor usa a API pública do PNCP.

## Publicação
1. Crie um repositório **público** no GitHub.
2. Envie todo o conteúdo desta pasta.
3. Em Settings → Pages, escolha `Deploy from a branch`, branch `main`, pasta `/root`.
4. Em Actions, rode `Riesppo Radar — coleta automática` uma vez com `Run workflow`.
5. Depois o cron roda de 2 em 2 horas.

A URL ficará parecida com `https://SEU-USUARIO.github.io/SEU-REPOSITORIO/`.

## Segurança de custo
Não há fallback para APIs pagas. A aplicação não deve receber cartão nem chave de API paga para funcionar.
