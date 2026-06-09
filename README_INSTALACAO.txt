INSTALAÇÃO RÁPIDA

1) Suba a API
- Crie uma conta no Render.com ou Railway.app.
- Crie um novo Web Service.
- Envie os arquivos: fenix_api.py, requirements.txt e Dockerfile.
- Configure variável de ambiente:
  FENIX_TOKEN=troque-este-token
  USD_BRL_FALLBACK=5.40
- Deploy.
- Teste no navegador: https://SUA-API/health

2) Google Sheets
- Abra o arquivo fenix_google_sheets_modelo_completo.xlsx no Google Sheets.
- Vá em Arquivo > Salvar como Planilhas Google.
- Vá em Extensões > Apps Script.
- Cole todo o conteúdo de apps_script_fenix.gs.
- Salve.
- Volte para a planilha e recarregue a página.
- Na aba Config:
  B2 = URL da sua API, ex: https://fenix-api.onrender.com
  B3 = cotação fallback, ex: 5.40
  B4 = mesmo token usado na API
- Na aba Monitor, coloque os itens na coluna Item.
- Menu Fenix > Atualizar itens.

3) Automático
- Menu Fenix > Criar trigger automático.
- Ele atualiza a cada 15 minutos.

OBSERVAÇÃO
Se o layout do Fenix Engine mudar, talvez seja necessário ajustar os seletores no arquivo fenix_api.py.
