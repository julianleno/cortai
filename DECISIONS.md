# Decisões Arquiteturais — Cortaí

Registro curto (formato ADR) das decisões consequentes tomadas durante o desenvolvimento. Ver também `.maestro/memory/DECISIONS.md` (ledger interno usado pelo agente de IA para continuidade entre sessões — mesmo conteúdo, mantido em sincronia).

## ADR-001 — Full HD (1080x1920) como única resolução de export na v1

- **Status:** aceita
- **Contexto:** o produto foi inspirado em ferramentas como Opus Clip/Clipzi, que oferecem export em FHD e 4K; o hardware disponível é CPU + GPU AMD RX 580, sem aceleração dedicada de vídeo por IA.
- **Decisão:** a v1 exporta apenas Full HD vertical (1080x1920); 4K (2160x3840) fica registrado como evolução futura, não agendada.
- **Motivo:** 4K aumenta significativamente o custo de processamento (transcrição, detecção de rosto, encode) sem mudar a lógica de produto — melhor validar a "inteligência" do pipeline primeiro.
- **Consequência:** entrega mais rápida das fases de maior valor (seleção automática, crop facial, legendas); 4K pode ser adicionado depois como opção de export adicional sem reescrever o pipeline.

## ADR-002 — Processamento de IA/ML em CPU; export pode usar encoder de hardware AMD

- **Status:** aceita
- **Contexto:** hardware é GPU AMD RX 580, sem CUDA/NVENC. `faster-whisper` (CTranslate2) só acelera via CUDA; detecção de rosto local também depende tipicamente de CUDA para GPU.
- **Decisão:** transcrição e detecção de rosto rodam em CPU. A etapa de exportação pode futuramente avaliar o encoder de hardware `h264_amf` da AMD via FFmpeg para reduzir tempo de render, sem que as demais etapas dependam disso.
- **Motivo:** não há caminho padrão de aceleração CUDA para hardware AMD nas bibliotecas escolhidas.
- **Consequência:** modelos Whisper menores (base/small) por padrão, para manter tempo de processamento razoável.

## ADR-003 — Gemini como provider primário de seleção de clipes, com fallback local sempre ativo

- **Status:** aceita
- **Contexto:** seleção automática de clipes precisa de uma camada de "inteligência" editorial, mas nunca pode depender exclusivamente de um serviço externo.
- **Decisão:** Gemini é o provider primário; NVIDIA NIM é o fallback secundário; regras determinísticas locais (pausas de fala, limites de frase) são sempre o fallback final, mesmo sem nenhuma chave de API configurada.
- **Motivo:** manter a promessa de que o "Clip Score" é uma estimativa editorial, nunca uma garantia — e nunca aceitar texto livre de um provider como resultado operacional sem validação de schema.
- **Consequência:** camada extra de código (validação de schema + retry + fallback em cadeia), mas o produto continua funcional mesmo sem rede ou com rate limit da API.

## ADR-004 — SQLite como persistência de jobs/clipes/templates

- **Status:** aceita
- **Contexto:** a implementação inicial usava uma fila em memória (dicionário Python), perdida a cada reinício do processo — incompatível com galeria de clipes, templates reutilizáveis e revisão/edição.
- **Decisão:** SQLite em arquivo local (`storage/cortai.db`), sem servidor de banco externo.
- **Motivo:** critérios de decisão do projeto priorizam "funcionar no Windows" e "fácil de instalar"; SQLite não exige instalação separada e é suficiente para uso single-user local.
- **Consequência:** mantém caminho aberto para trocar por Postgres se o produto evoluir para multiusuário — essa evolução não está bloqueada por essa escolha.

## ADR-005 — Crop por rosto é um único enquadramento estável por clipe, não dinâmico por frame

- **Status:** aceita
- **Contexto:** um crop verdadeiramente dinâmico por frame (acompanhando o rosto em tempo real dentro do clipe) exigiria um filtro FFmpeg com posição variando no tempo e um pipeline de suavização temporal bem mais complexo.
- **Decisão:** o sistema amostra ~12 frames ao longo do clipe, detecta o rosto principal em cada um, e usa a **mediana** das posições como um único crop fixo para o clipe inteiro. Fallback para crop central puro quando nenhum rosto é detectado em nenhuma amostra.
- **Motivo:** um crop que nunca se move elimina "crop nervoso" por construção — já satisfaz o requisito mais importante (evitar crops nervosos). Rastreamento dinâmico por frame fica como evolução isolada e futura.
- **Consequência:** clipes com mudança real de cena (ex: dois falantes em posições bem diferentes na tela) podem ter enquadramento não-ideal para um deles durante parte do clipe — aceitável para o escopo priorizado (podcasts/entrevistas, câmera geralmente estática).

## ADR-006 — Detecção de rosto via `cv2.FaceDetectorYN` (YuNet), não Haar Cascade

- **Status:** aceita
- **Contexto:** o plano original assumia Haar Cascade (`cv2.CascadeClassifier`), incluído no pacote `opencv-python` sem downloads extras. A versão atual instalada do OpenCV (5.0) removeu essa API dos bindings Python.
- **Decisão:** usar `cv2.FaceDetectorYN` (modelo YuNet, ONNX). Modelo (~227KB, Apache-2.0) baixado do repositório oficial OpenCV Zoo e commitado em `src/cortai/models/`, seguindo o mesmo padrão já usado para a marca d'água padrão.
- **Motivo:** não há alternativa Haar disponível na versão instalada; YuNet é, de qualquer forma, a abordagem atualmente recomendada pelo próprio OpenCV (mais precisa, ainda leve o suficiente para CPU).
- **Consequência:** o pipeline depende de um arquivo de modelo commitado no repositório (não só código), mas segue o precedente já estabelecido pela marca d'água.

## ADR-007 — Legendas em ASS (Advanced SubStation Alpha), não SRT com estilo fixo

- **Status:** aceita
- **Contexto:** a primeira versão do pipeline gerava um SRT simples com um único estilo fixo aplicado via `force_style` do FFmpeg — insuficiente para os 6 presets de legenda exigidos (cores/contorno/sombra distintos, destaque de palavra-chave, destaque progressivo palavra por palavra).
- **Decisão:** gerar arquivos `.ass` diretamente, com um `Style` por preset e tags de override inline (incluindo karaoke nativo `\kf` para o destaque progressivo).
- **Motivo:** só o formato ASS dá acesso nativo, via libass (já usado pelo filtro `subtitles` do FFmpeg), às features necessárias sem gambiarras.
- **Consequência:** módulo de legendas mais complexo, mas cada preset é uma composição direta de campos de estilo padrão, sem pós-processamento de texto.

## ADR-008 — Script de instalação em PowerShell (`start.ps1`) com wrapper `.bat` para duplo clique, `uv` opcional

- **Status:** aceita
- **Contexto:** o critério de instalação exige "idealmente um único comando ou duplo clique" no Windows, verificando Python, FFmpeg, ffprobe, dependências, diretórios e porta ocupada.
- **Decisão:** `start.ps1` concentra toda a lógica de verificação/instalação/execução; `start.bat` é um wrapper de uma linha que invoca o PowerShell com política de execução liberada, permitindo duplo clique direto. O script usa `uv` (mais rápido) se disponível no PATH, com fallback automático para `python -m venv` + `pip`.
- **Motivo:** PowerShell é mais expressivo para as verificações necessárias (comandos, portas, arquivos) do que um `.bat` puro; o wrapper `.bat` existe só porque arquivos `.ps1` não rodam com duplo clique por padrão no Windows (abrem no Notepad).
- **Consequência:** testado do zero (removendo `.venv` e rodando o script) até a interface responder via HTTP real — ver `PROGRESS.md`.

## ADR-009 — Active speaker detection: heurística por segmento de fala, não análise de movimento labial

- **Status:** aceita
- **Contexto:** validação com uma URL real revelou dois bugs corrigidos à parte (ver seção de bugs em `PROGRESS.md`); em seguida, o usuário pediu active speaker detection real (Fase 9, opcional no MVP original). A abordagem de precisão máxima cruzaria movimento labial de cada rosto com a energia do áudio quadro a quadro — mais precisa, porém significativamente mais complexa e lenta em CPU.
- **Decisão:** dividir o clipe em sub-janelas usando as fronteiras de fala já detectadas (Fase 2), detectar o rosto dominante de forma independente em cada sub-janela (reaproveitando a mediana da Fase 4), e concatenar os trechos cortados/recortados — sem análise de movimento labial.
- **Motivo:** cobre o caso comum (2 pessoas se revezando) sem uma nova camada de visão computacional, reaproveitando quase toda a infraestrutura já construída; roda em CPU.
- **Consequência:** as janelas são sempre contíguas cobrindo o clipe inteiro — uma pausa real de fala entre janelas é absorvida na janela seguinte, não descartada, porque descartá-la encurtaria o vídeo final e dessincronizaria as legendas. Novo `layout="active_speaker"` disponível em templates, ao lado de "auto" e "center". Prova real: um rosto real (imagem de teste oficial do OpenCV) composto em duas posições de tela bem diferentes ao longo de um vídeo sintético — o crop detectado desloca de x≈0 para x≈707 entre as janelas, confirmando que a detecção roda de forma independente por janela.

## ADR-010 — Correção de transcrição em duas camadas: vocabulário (fuzzy match) no nível do job + overrides manuais por palavra no nível do clipe

- **Status:** aceita
- **Contexto:** validação com URL real revelou que siglas/nomes próprios (ex.: "ANAC"/"VOEPASS") às vezes saem incorretos na transcrição ("ANAK"/"VOEPAZ"), e o usuário quer poder corrigir isso antes de a legenda ser queimada no vídeo. Perguntado explicitamente se preferia (a) só um glossário reutilizável, (b) só um editor manual, ou (c) os dois — usuário escolheu **os dois**.
- **Decisão:** duas camadas independentes, aplicadas em sequência (vocabulário primeiro, depois overrides manuais, que sempre vencem): (1) `vocabulary` — lista de termos corretos por vídeo (`jobs.vocabulary_json`, semeada a partir do template escolhido + campo livre no formulário de envio), corrigida automaticamente via fuzzy matching (`difflib.SequenceMatcher`, limiar 0.65) contra cada palavra da transcrição; (2) `word_overrides` — dicionário `{índice_da_palavra: texto_corrigido}` por clipe (`clips.word_overrides_json`), editável numa tela dedicada (`/clips/<id>/edit-transcript`) que mostra cada palavra do clipe como um campo de texto individual, preservando 100% os timestamps (só o texto pode mudar, nunca a contagem/tempo de palavras).
- **Motivo:** vocabulário fica no nível do job (não do template, não do cache de transcrição) porque é uma propriedade do conteúdo do vídeo, reutilizável entre templates diferentes ao re-renderizar; overrides ficam no nível do clipe (indexados pela lista de palavras já fatiada do clipe, não do vídeo inteiro) porque é isso que a tela de edição naturalmente enumera. Edição por campo-de-texto-por-palavra (não textarea livre) elimina por construção qualquer risco de dessincronizar a legenda.
- **Consequência:** `render_clip_from_source()` aplica `vocabulary_module.apply_corrections()` (vocabulário → depois overrides) antes de fatiar/renderizar. Re-renderizar um clipe após editar dispara automaticamente uma nova renderização com as mesmas correções aplicadas.

## ADR-011 — Novo layout "fundo desfocado": vídeo inteiro visível sobre cópia ampliada e borrada de si mesmo

- **Status:** aceita
- **Contexto:** na mesma mensagem do vocabulário/editor de legenda, o usuário pediu uma opção para vídeos sem pessoa aparecendo logo no início: em vez de cropar a cena (que pode cortar demais quando ainda não há rosto para ancorar o enquadramento), mostrar o vídeo inteiro na tela vertical, com um fundo desfocado preenchendo as bordas — a técnica clássica de "blurred background" usada em ferramentas como Opus Clip/Clipzi.
- **Decisão:** novo `layout="blurred_background"`, ao lado de `"auto"`, `"center"` e `"active_speaker"`. Implementado com um único filtro FFmpeg: o vídeo de origem é dividido (`split`) em dois ramos — um escalado para preencher todo o quadro vertical (`scale...force_original_aspect_ratio=increase` + `crop` + `gblur`), outro escalado para caber inteiro dentro do quadro sem cortar nada (`scale...force_original_aspect_ratio=decrease`) — e o segundo é sobreposto centralizado no primeiro (`overlay`).
- **Motivo:** não depende de detecção de rosto (não precisa ancorar nada), então funciona igualmente bem em cenas sem pessoa; é a abordagem correta para o problema descrito (evitar cortar demais a cena), e reaproveita a mesma cauda de pipeline (`render_vertical_clip`) sem exigir nenhuma mudança estrutural — apenas mais um ramo de `layout`.
- **Consequência:** como não há rosto ancorado, a posição da legenda usa sempre o fallback padrão (embaixo), igual ao caso "nenhum rosto detectado" dos outros layouts. Prova real: vídeo sintético renderizado, frame extraído e inspecionado via `cv2`/`numpy` — a faixa de fundo (topo/base do frame) tem gradiente de pixel visivelmente menor (mais suave, borrada) que a faixa central (cena original nítida), confirmando que o efeito de desfoque está de fato sendo aplicado e a cena central permanece sem corte.

## ADR-012 — Active Speaker com análise de movimento labial (lip-sync)

- **Status:** aceita
- **Contexto:** a heurística original do `active_speaker` (ADR-009) escolhia o maior rosto detectado em cada janela de fala. Em quadros abertos com 2 participantes simultâneos na tela (podcasts/entrevistas), isso favorecia a pessoa mais próxima da câmera ou com cabeça maior, mesmo quando a outra pessoa era a falante ativa.
- **Decisão:** implementar detecção multi-rosto (`detect_all_faces`), rastreamento temporal por centroid (`track_faces_in_window`) e pontuação de movimento labial (`compute_lip_movement_score`). O algoritmo extrai a ROI da boca usando as marcas faciais do YuNet (`right_mouth`/`left_mouth`), calcula a variação temporal de abertura/largura labial e a diferença média de intensidade de pixels entre frames consecutivos, e elege a pista de rosto com maior score de atividade labial na janela de fala.
- **Motivo:** resolve o problema de sobreposição e participantes múltiplos na tela sem adicionar dependências pesadas de PyTorch/CUDA, rodando 100% em CPU via OpenCV + NumPy em poucos milissegundos por janela.
- **Consequência:** em janelas onde nenhum rosto apresenta movimento labial perceptível (fala pausada ou silêncio), o sistema recai com segurança para a seleção por área mediana de rosto (fallback ADR-009) ou crop central.

## ADR-013 — Layout "Manter todos os rostos" (split-screen empilhado)

- **Status:** aceita
- **Contexto:** em tomadas horizontais (16:9) com 2 falantes visíveis simultaneamente (entrevistas, podcasts de estúdio), o usuário pode preferir ver os 2 participantes ao mesmo tempo na tela vertical 9:16 em vez de cropar apenas um deles ou alternar o corte.
- **Decisão:** novo `layout="all_faces"` (ou `"stacked_faces"`), ao lado de `"auto"`, `"center"`, `"active_speaker"` e `"blurred_background"`. O algoritmo analisa a presença facial, seleciona os 2 rostos dominantes no vídeo, os ordena horizontalmente (esquerda e direita) e renderiza um vídeo vertical de $1080 \times 1920$ composto por 2 painéis empilhados ($1080 \times 960$ cada) através de um filtro FFmpeg (`split` + `crop` + `scale` + `vstack`).
- **Motivo:** oferece visualização simultânea para vídeos de diálogo sem acréscimo significativo de complexidade de renderização.
- **Consequência:** se menos de 2 rostos distintos forem detectados no vídeo, o sistema recai automaticamente para o layout vertical de 1 rosto (`"auto"`), evitando criar um painel vazio.

## ADR-014 — Upload de arquivo de vídeo local (.mp4, .mkv, .mov, .avi, .webm)

- **Status:** aceita
- **Contexto:** a primeira versão do sistema só aceitava URLs públicas do YouTube. Criadores de conteúdo que gravam localmente (câmeras, gravadores de tela, estúdio) precisam processar seus próprios arquivos sem precisar subir o vídeo para o YouTube primeiro.
- **Decisão:** permitir o upload direto de arquivos de vídeo local na rota `/` (`POST`) do Flask com o formulário em `enctype="multipart/form-data"`. O arquivo é salvo com nome sanitizado e único em `storage/downloads/local_<uuid>_<nome_original>` e registrado no job como `source_video_path`. Na execução do job em background (`process_suggestion_job`), a etapa `download_video()` é detectada e pulada automaticamente se o arquivo já existir no disco.
- **Motivo:** oferece paridade completa entre vídeos do YouTube e vídeos gravados localmente, mantendo a arquitetura local-first e sem acoplamentos.
- **Consequência:** a transcrição, a seleção por IA/fallback, o crop de rosto e a geração de legendas operam exatamente da mesma forma tanto para vídeos locais quanto para vídeos baixados do YouTube.

## ADR-015 — Botões de cópia rápida do Título e Gancho nos cards da galeria

- **Status:** aceita
- **Contexto:** ao postar os clipes em redes como TikTok, Reels ou Shorts, o criador de conteúdo precisa copiar o título e a frase de impacto (gancho/*hook*) sugeridos pela IA. Fazer a seleção manual de texto é lento e propenso a erros de cópia.
- **Decisão:** adicionar botões dedicados de **"Copiar Título"** e **"Copiar Gancho"** em cada card de clipe no template `status.html`. A cópia é realizada via a API nativa do navegador `navigator.clipboard.writeText()`, com feedback visual instantâneo (o botão altera para ícone de *check* e exibe "Copiado!" por 2 segundos).
- **Motivo:** melhora significativamente a usabilidade e a agilidade na hora de publicar os clipes exportados nas redes sociais.
- **Consequência:** zero dependências externas adicionais no frontend; utiliza recursos nativos do HTML5/JavaScript.

## ADR-016 — Controle avançado de exportação e performance FFmpeg no template

- **Status:** aceita
- **Contexto:** o pipeline de exportação original usava configurações estáticas do FFmpeg (`libx264`, `preset=medium`, `crf=23`). O usuário precisa personalizar a velocidade de encode (ex: `ultrafast` para prévias rápidas) ou utilizar aceleradores por hardware (como GPU AMD `h264_amf` ou NVIDIA `h264_nvenc`).
- **Decisão:** expor seletores na interface de criação/edição de templates (`template_form.html`) para personalizar:
  - Codec de Vídeo: `libx264` (CPU), `h264_amf` (AMD GPU acelerada), `hevc_amf`, `h264_nvenc`, `hevc_nvenc`.
  - Preset de velocidade: `ultrafast` até `veryslow`.
  - Qualidade CRF: de `15` a `35`.
  Os parâmetros são validados em `templates.py` e repassados pelas rotas `render_kwargs_from_template` e `render_vertical_clip` até o comando de exportação do FFmpeg.
- **Motivo:** proporciona controle fino sobre velocidade de renderização e qualidade visual, permitindo aproveitar placas de vídeo modernas (AMD/NVIDIA).
- **Consequência:** mantém valores padrão seguros (`libx264`, `medium`, `crf=23`) se nada for alterado pelo usuário.

## ADR-017 — Correção de enquadramento facial, quebra de legenda ASS e seleção de aspecto (9:16 vs 16:9)

- **Status:** aceita
- **Contexto:** testes em vídeos reais revelaram três falhas visuais críticas: 1) o crop cortava rostos localizados perto das bordas laterais ao tentar centralizar cegamente; 2) legendas ASS estouravam as margens da tela por usar `WrapStyle: 2` (sem quebra de linha); 3) o título de fallback no banco gerava `"Trecho selecionado automaticamente"`; e 4) não havia opção para clipes horizontais 16:9 de até 5 minutos.
- **Decisão:**
  1. `face_tracking.py`: atualizar `compute_face_crop_window()` com margem de segurança ajustando o ponto `x` para que o retângulo do rosto `[left, right]` fique 100% contido dentro da janela de crop.
  2. `subtitles.py`: alterar `WrapStyle` de `2` para `0` (Smart wrapping) e expandir a margem vertical `margin_v` para 12% da altura em `position="bottom"`.
  3. `clip_selection.py`: gerar títulos legíveis com as primeiras palavras do trecho em vez da string genérica.
  4. `index.html` & `app.py`: adicionar o seletor **Formato do Vídeo** (`9:16` com limite de 60s / 1 min vs `16:9` com limite de 300s / 5 min).
- **Motivo:** eliminação dos defeitos visuais relatados nos testes reais e flexibilidade para gerar vídeos curtos verticais ou horizontais mais longos.
- **Consequência:** eliminação de cortes de rosto nas bordas, legendas 100% visíveis dentro da área segura e títulos informativos nos clipes.

## ADR-018 — Seletor visual de legendas estilizadas na interface principal

- **Status:** aceita
- **Contexto:** o motor do Cortaí possuía 6 presets de legendas estilizadas em `.ASS` (`progressive-word-highlight`, `white-bold`, `yellow-bold`, `white-yellow-keyword`, `white-red-highlight`, `cyan-shadow`), mas eles só podiam ser configurados através da edição manual de templates. No formulário de envio de vídeos (`index.html`), o usuário não conseguia escolher ou pré-visualizar o estilo desejado.
- **Decisão:**
  1. `subtitles.py`: criar e expor a estrutura `CAPTION_PRESETS_METADATA` contendo labels amigáveis, amostra textual, CSS de renderização e descrição de cada preset.
  2. `index.html`: implementar um seletor visual interativo em grade de cards com rádio buttons, onde o usuário visualiza o efeito exato do estilo (ex: destaque palavra-por-palavra Karaoke, ciano com sombra, bordas amarelas/vermelhas) e escolhe o estilo direto na submissão.
  3. `template_form.html`: incluir caixa de prévia dinâmica via JS ao criar/editar templates.
  4. `app.py`: capturar o `caption_preset` do formulário e repassá-lo na renderização do vídeo.
- **Motivo:** proporciona uma experiência visual rica onde o usuário escolhe e pré-visualiza a legenda estilizada diretamente antes de iniciar o processamento.
- **Consequência:** controle total sobre a estética das legendas na tela inicial.

## ADR-019 — Capitalização de legendas, suporte a vídeos sem fala e layouts de moldura

- **Status:** aceita
- **Contexto:** solicitações adicionais do usuário: 1) opção de capitalização das legendas em `TitleCase` mantendo artigos e preposições (`o/a/de/e/em/por...`) em minúsculas; 2) suporte a vídeos sem áudio ou sem fala sem falhar a renderização, aplicando legenda estática de capa; 3) criação de layouts inspirados em `docs/examples` com o vídeo original inteiro sem cortes dentro de molduras modernas (`card_frame` e `header_footer_frame`).
- **Decisão:**
  1. `subtitles.py`: adicionar a função `format_word_casing()` com a lista `_EXCLUDED_LOWERCASE_WORDS` para tratar conectivos e preposições em minúsculas no modo `TitleCase`. Se a transcrição estiver vazia (sem áudio de fala), gerar automaticamente uma legenda estática cobrindo toda a duração do clipe.
  2. `templates.py` & `core.py`: adicionar os layouts `card_frame` ("Moldura Social Card estilo Twitter/Instagram") e `header_footer_frame` ("Moldura Editorial Topo Título + Rodapé Marca").
  3. `index.html` & `template_form.html`: incluir seletores de capitalização (`UPPERCASE` vs `TitleCase`) e opções de layout de moldura na interface.
- **Motivo:** atende à formatação gramatical elegante de títulos/legendas, evita quebra de vídeos sem áudio e permite publicar vídeos completos sem cortar a cena original.
- **Consequência:** maior flexibilidade estética e suporte a 100% dos tipos de mídia.

## ADR-020 — Processamento em lote, design system Airbnb, player de prévia e ajustador de enquadramento Clipzy

- **Status:** aceita
- **Contexto:** solicitações do usuário: 1) processamento de vídeos em lote (submeter até 10 URLs ou múltiplos arquivos de vídeo); 2) ver prévia do vídeo antes de baixar; 3) renovação geral da UI com Design System inspirado no Airbnb e logo simpática em SVG; 4) tela de carregamento animada por etapas no estilo Opus Clip; 5) ferramenta interativa de ajuste lateral do enquadramento do corte no estilo Clipzy.
- **Decisão:**
  1. `app.py` & `projects.html`: implementar recepção de múltiplas URLs ou múltiplos arquivos de vídeo em `/submit`, enfileirando tarefas e redirecionando para a nova tela de gerenciamento de lotes `/projects`.
  2. `base.html`: construir o Design System Airbnb (paleta coral `#FF385C`, modo escuro sofisticado `#121214`, cards arredondados, fonte *Plus Jakarta Sans*) e integrar a logo em SVG do rolo de filme sorridente Cortaí.
  3. `status.html`: implementar a tela de progresso por etapas animadas estilo Opus Clip (com gauge circular e 5 estágios: Download -> Transcrição -> Lip-Sync -> Legendas -> Exportação), embutir players de vídeo HTML5 `<video controls>` nos cards de clipes para assistir prévia diretamente antes do download, e criar o modal interativo de ajuste lateral do corte 9:16 estilo Clipzy com rota de re-renderização em `/clips/<clip_id>/adjust-crop`.
- **Motivo:** eleva o Cortaí a um padrão de produto comercial completo (nível Opus Clip / Clipzy / Vizard), garantindo alta produtividade em lote e controle total sobre o enquadramento.
- **Consequência:** interface extremamente moderna e rápida, suporte a lotes ilimitados e experiência de edição visual sem fricção.

## ADR-021 — Progresso dinâmico contínuo, ferramenta de correção de texto e controle de jobs (Pausar/Retomar/Cancelar)

- **Status:** aceita
- **Contexto:** feedback do usuário: 1) o indicador de carregamento de progresso pulava de 10% para 30% parecendo estático; 2) o campo "Termos importantes" era genérico e devia ser substituído por uma ferramenta de correção de texto e dicionário IA; 3) necessidade de controlar o fluxo de execução dos vídeos (pausar, retomar e cancelar processamentos).
- **Decisão:**
  1. `status.html`: implementar um loop de micro-interpolação dinâmica de progresso no frontend (com incrementos contínuos a cada 250ms e descrições detalhadas da sub-etapa ativa), eliminando a sensação de congelamento.
  2. `index.html`: substituir o campo genérico de termos pela **Ferramenta de Correção de Texto & Dicionário IA Integrada**, contendo chips interativos de atalho (`ANAC`, `VOEPASS`, `Cortaí`, `IA`, `YouTube`, `ChatGPT`) e campo inteligente de substituição.
  3. `app.py` & `db.py`: adicionar suporte aos estados `paused` e `cancelled` e criar rotas `/jobs/<job_id>/pause`, `/jobs/<job_id>/resume` e `/jobs/<job_id>/cancel`, com verificação de estado em tempo de execução nas threads de processamento.
- **Motivo:** proporciona um indicador de progresso fluído e responsivo, controle operacional completo sobre jobs em execução e substituição automática de erros de transcrição.
- **Consequência:** controle total sobre a execução e experiência de carregamento 100% dinâmica.

## ADR-022 — Remoção imediata de projetos cancelados, purga de processos órfãos e exclusão manual

- **Status:** aceita
- **Contexto:** relatos de defeitos do usuário: 1) ao cancelar um projeto, ele continuava armazenado como registro; o usuário deseja que projetos cancelados sumam completamente da interface sem guardar histórico; 2) acúmulo de tarefas travadas da sessão anterior ao reiniciar o servidor (13 processos órfãos no banco).
- **Decisão:**
  1. `db.py`: adicionar a função `cleanup_orphan_jobs(db_path)` executada na inicialização do banco (`init_db`), purgando tarefas não concluídas (`queued`, `processing`, `paused`, `cancelled`) deixadas por reinicializações/quedas do servidor.
  2. `db.py` & `app.py`: implementar a exclusão imediata de projetos cancelados (`db.delete_job`), removendo-os totalmente da tabela de jobs e clips.
  3. `projects.html`: adicionar o botão **`[Excluir Projeto]`** para remover manualmente qualquer projeto da lista.
- **Motivo:** garante que o banco de dados e a interface estejam sempre limpos, sem acúmulo de processos fantasma ou históricos indesejados.
- **Consequência:** inicialização do servidor 100% limpa e remoção instantânea de vídeos cancelados ou descartados.

## ADR-023 — Neon Cyber Design System, alternador claro/escuro, editor Opus Clip e compilação EXE

- **Status:** aceita
- **Contexto:** solicitações do usuário: 1) logo nova que remeta a cinema e vídeos curtos; 2) opção de alternar entre Tema Claro e Tema Escuro; 3) criar o próprio Design System ("Neon Cyber") eliminando referências a Airbnb; 4) remover chips de exemplo demo e implementar o editor interativo de transcrição estilo Opus Clip (`/clips/<clip_id>/edit-transcript`); 5) suporte a ajuste lateral dinâmico de enquadramento 9:16 por trecho de vídeo (estilo Clipzy); 6) customização avançada de legendas (fontes Montserrat, Impact, Anton, Bebas Neue, Poppins, Inter, Outfit, Roboto); 7) script para empacotar a aplicação em executável standalone `.exe`.
- **Decisão:**
  1. `base.html`: redesenhar a logo SVG em estilo de claquete de cinema neon + rolo de filme, implementar o alternador dinâmico de temas (Claro/Escuro) via `localStorage` e construir o **Neon Cyber Design System** (acentos ciano `#06B6D4`, magenta `#FF007F`, superfícies obsidian e glassmorfismo).
  2. `edit_transcript.html`: reformular o editor para o layout estilo Opus Clip com vídeo lado a lado e grade interativa de palavras com timestamps.
  3. `index.html` & `template_form.html`: incluir seletores para fontes populares (Impact, Montserrat, Anton, Bebas Neue, Poppins, Inter, Outfit, Roboto) e seleção destacada das Molduras Sociais (`card_frame` e `header_footer_frame`).
  4. `build_exe.py`: criar script de automação PyInstaller para gerar o executável `Cortai.exe` no Windows.
- **Motivo:** eleva o produto ao padrão de software comercial profissional autônomo, com identidade visual cyberpunk própria e empacotamento desktop sem dependências externas.
- **Consequência:** aplicativo com identidade visual própria e possibilidade de distribuição como arquivo `.exe`.

## ADR-024 — Retorno ao Airbnb Design System, Gerenciador de Configurações (/settings), Provedores OpenAI & Anthropic e Garantia 100% Local

- **Status:** aceita
- **Contexto:** solicitações do usuário: 1) retorno ao **Airbnb Design System** (paleta Coral Pink `#FF385C`, modo escuro `#121214`, cards arredondados e logo do rolo de filme sorridente Cortaí em SVG); 2) criação da tela de **Configurações de API Keys no UI (`/settings`)** para cadastro de chaves diretamente no navegador; 3) inclusão dos provedores **OpenAI (GPT-4o)** e **Anthropic (Claude 3.5)**; 4) garantia de **Processamento 100% Offline / Local** quando nenhuma chave de API estiver cadastrada; 5) correção do erro `AttributeError: module 'cortai.clip_selection' has no attribute 'suggest_clips_for_video'`.
- **Decisão:**
  1. `base.html`: restaurar o Airbnb Design System com tom Coral Pink `#FF385C`, logo do rolo de filme sorridente Cortaí e menu com link para `/settings`.
  2. `db.py`: criar a tabela SQLite `settings` e métodos `get_setting` / `set_setting` para persitência local das API Keys.
  3. `providers/`: criar `openai.py` e `anthropic.py` com integração HTTPX.
  4. `clip_selection.py`: definir alias `suggest_clips_for_video = suggest_clips`, orquestrar o fallback em cadeia (Gemini → OpenAI → Anthropic → NVIDIA NIM → Local) e garantir execução 100% offline via `segmentation.py` quando nenhuma chave for fornecida.
- **Motivo:** garante usabilidade máxima do executável portátil `.exe` para qualquer usuário cadastrar chaves no UI, oferece suporte a OpenAI/Claude e mantêm a privacidade total no modo local.
- **Consequência:** experiência de uso visual premium e flexibilidade total de provedores de IA.







## ADR-025 — Abertura como software livre, novo nome "Cortaí" e identidade visual própria

- **Status:** aceita
- **Contexto:** o projeto foi aberto como open source (GPL-3.0). O nome "Open Clip" e o pacote `open_clip` conflitavam com a biblioteca OpenCLIP, e a interface reaproveitava cores e referências de marcas de terceiros.
- **Decisão:**
  1. Renomear o projeto para **Cortaí** ("corta aí") e o pacote Python para `cortai` (repositório `julianleno/cortai`).
  2. Criar uma identidade visual própria: marca "play cortado", paleta Caju `#FF6A2B` / Mar `#12B5A6` / Sol `#FFC93C` sobre Noite `#111113` ou Areia `#FBF7F2`, tipografia Bricolage Grotesque + DM Sans. Guia em `docs/brand/`.
  3. Texto sobre o Caju sempre em Tinta `#16140F` (contraste 6,4:1).
  4. Marca d'água padrão passa a ser um PNG com transparência (marca + logotipo).
- **Consequência:** nome e visual sem conflito com outros projetos. O banco padrão passa a se chamar `storage/cortai.db`; instalações antigas podem renomear o arquivo `open_clip.db` ou apontar `DB_PATH` para ele.
