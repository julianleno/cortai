# Contribuindo com o Cortaí

Obrigado pelo interesse em contribuir! 🎬 Toda ajuda é bem-vinda: correção de bugs, novas funcionalidades, melhorias de documentação, traduções e testes.

## Antes de começar

- Leia o [Código de Conduta](CODE_OF_CONDUCT.md). Ao participar, você concorda em segui-lo.
- Procure nas [issues](../../issues) se o assunto já está sendo discutido.
- Para mudanças grandes (novo pipeline, troca de dependência, mudança de arquitetura), **abra uma issue primeiro** para alinharmos a ideia antes do código.
- Decisões de arquitetura ficam registradas em [`DECISIONS.md`](DECISIONS.md). Vale a leitura antes de mexer no núcleo.

## Reportando bugs

Abra uma issue usando o modelo **Bug report** e inclua:

- Sistema operacional, versão do Python e do FFmpeg (`python --version`, `ffmpeg -version`)
- Passo a passo para reproduzir
- O que você esperava e o que aconteceu
- Logs relevantes do terminal (remova qualquer chave de API ou dado pessoal)

> Encontrou uma vulnerabilidade de segurança? **Não abra uma issue pública.** Veja o [SECURITY.md](SECURITY.md).

## Preparando o ambiente de desenvolvimento

Pré-requisitos: **Python 3.12+** e **FFmpeg** (com `ffprobe`) no PATH.

**Windows**

```powershell
git clone https://github.com/julianleno/cortai.git
cd cortai
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pip install pytest black flake8 mypy
```

**Linux / macOS**

```bash
git clone https://github.com/julianleno/cortai.git
cd cortai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install pytest black flake8 mypy
```

Copie `.env.example` para `.env` e ajuste o que precisar. Chaves de API são **opcionais**: sem elas, a seleção de clipes roda 100% local.

Para rodar a aplicação: `python -m cortai.app` e abra http://127.0.0.1:5000.

## Rodando os testes

```bash
python -m pytest tests/ -v
```

Todo PR deve manter a suíte passando. Se você corrigiu um bug, adicione um teste que falharia sem a correção.

## Estilo de código

- Formate com **black** e verifique com **flake8** antes de abrir o PR:
  ```bash
  black src tests
  flake8 src tests
  ```
- Prefira funções pequenas e nomes descritivos. Comentários e docstrings podem ser em português ou inglês.
- Não commite vídeos, áudios, legendas geradas, bancos `.db`, a pasta `storage/` nem o arquivo `.env`.

## Mensagens de commit

Usamos o padrão [Conventional Commits](https://www.conventionalcommits.org/pt-br/):

- `feat:` nova funcionalidade
- `fix:` correção de bug
- `docs:` documentação
- `test:` testes
- `refactor:` refatoração sem mudança de comportamento
- `chore:` manutenção (dependências, build etc.)

Exemplo: `fix: corrige legenda cortada em vídeos 16:9`

## Abrindo um Pull Request

1. Faça um fork e crie uma branch a partir da `main` (ex.: `fix/legenda-cortada`).
2. Faça commits pequenos e focados.
3. Rode os testes e o linter.
4. Abra o PR preenchendo o modelo, explicando **o quê** mudou e **por quê**. Prints ou vídeos curtos ajudam muito em mudanças de interface.
5. Responda aos comentários da revisão. Depois de aprovado, o mantenedor faz o merge.

## Licença das contribuições

O Cortaí é distribuído sob a licença [GPL-3.0](LICENSE). Ao enviar uma contribuição, você concorda que ela será licenciada nos mesmos termos.
