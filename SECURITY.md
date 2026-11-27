# Política de Segurança

## Versões com suporte

Correções de segurança são aplicadas apenas na versão mais recente do Cortaí (branch `main`).

| Versão | Suporte |
|---|---|
| 1.5.x | ✅ |
| < 1.5 | ❌ |

## Como reportar uma vulnerabilidade

**Não abra uma issue pública** para relatar falhas de segurança.

Use o reporte privado do GitHub: na aba **Security** deste repositório, clique em **Report a vulnerability**. Inclua:

- Descrição do problema e o impacto possível
- Passo a passo para reproduzir (ou uma prova de conceito)
- Versão do Cortaí, sistema operacional e versão do Python

Você deve receber uma primeira resposta em até 7 dias. Assim que a correção for publicada, daremos o crédito a quem reportou (se quiser).

## Boas práticas para quem usa

O Cortaí foi feito para rodar **localmente**:

- Mantenha `FLASK_HOST=127.0.0.1` (padrão). Não exponha a porta da aplicação na internet nem na rede local sem uma camada de autenticação na frente.
- Mantenha `FLASK_DEBUG=False` fora do desenvolvimento.
- Chaves de API (OpenAI, Anthropic, Gemini, NVIDIA NIM) ficam no seu `.env` ou no banco local. Nunca commite o `.env` nem a pasta `storage/`.
