# Tasks — Taqui o Contexto

> Baseado em [prd.md](prd.md). Cada tarefa indica de quais outras depende (**dep**) e o que precisa estar pronto para considerá-la concluída (**pronto quando**).
> Tarefas marcadas com 🔒 dependem de uma questão em aberto do PRD.

## Fase 0 · Fundação

- [x] **T-01 · Projeto Python**
  - `pyproject.toml` com FastAPI, Uvicorn, Jinja2, SQLAlchemy (ou asyncpg), Alembic, pydantic-settings.
  - Grupo opcional `pipeline` com numpy e gensim (fora da aplicação web).
  - Ferramentas: ruff (lint e formatação), pytest, pytest-asyncio.
  - **Pronto quando:** `uvicorn app.main:app` sobe e `GET /health` responde 200; `ruff check` e `pytest` rodam sem erro.

- [x] **T-02 · Estrutura por feature**
  - Criar `app/core`, `app/auth`, `app/games`, `app/guesses`, `app/hints`, `app/prizes`, `app/admin`, `pipeline/`, `migrations/`, `static/`.
  - Cada feature com `router.py`, `service.py`, `models.py` e, se tiver telas, `templates/`.
  - `main.py` inclui os routers de todas as features.
  - **dep:** T-01

- [x] **T-03 · Configuração**
  - `app/core/config.py` com pydantic-settings: URL do banco, URL e chaves do Supabase, segredo do JWT, `ADMIN_EMAILS`, fuso (`America/Sao_Paulo`).
  - `.env.example` documentando cada variável.
  - **dep:** T-02

- [ ] **T-04 · Supabase local**
  - Supabase CLI (`supabase init` / `supabase start`) para desenvolvimento local.
  - Habilitar a extensão `vector` (pgvector).
  - Configurar o provedor Google no Supabase Auth (local e produção).
  - ✅ Supabase local rodando (`npx supabase@2 start`), com serviços não usados desligados. ⏳ Falta criar as credenciais OAuth do Google.
  - **Pronto quando:** o app conecta no Postgres local e o login com Google funciona no painel do Supabase.

- [x] **T-05 · Layout base e front**
  - `core/templates/base.html` com HTMX, CSS base (tokens de cor, tipografia, espaçamento) e cabeçalho com login/logout.
  - `Jinja2Templates` com `PrefixLoader`: cada feature tem a sua pasta `templates/`, e os templates são referenciados como `feature/arquivo.html` (por exemplo, `core/base.html`), sem risco de colisão de nomes.
  - Pasta `static/` servida pelo FastAPI.
  - **dep:** T-02

## Fase 1 · Banco de dados

- [x] **T-06 · Migrations**
  - Alembic configurado em `migrations/`.
  - Tabelas: `profiles`, `vocabulary`, `games`, `game_members`, `word_rankings`, `guesses`, `hints`, `prizes`, com todas as chaves, enums e restrições do PRD (seção 6).
  - Garantir no banco que só existe **um jogo ativo** (índice único parcial em `games` para `status IN ('locked', 'open')`).
  - Garantir que os períodos de jogos não se sobrepõem (restrição `EXCLUDE` com `tstzrange`).
  - **dep:** T-04
  - **Pronto quando:** `alembic upgrade head` cria tudo do zero e `alembic downgrade base` desfaz.

- [x] **T-07 · Proteger as tabelas da API pública do Supabase**
  - O Supabase expõe as tabelas pela API REST com a chave pública (`anon`). Sem proteção, qualquer pessoa leria `games.secret_word` e `word_rankings`.
  - Ativar RLS em todas as tabelas, sem políticas para `anon` e `authenticated`. Só o backend (conexão direta ao Postgres) acessa os dados.
  - **dep:** T-06
  - **Pronto quando:** uma chamada à API REST do Supabase com a chave `anon` não devolve nenhuma linha de nenhuma tabela.

- [x] **T-08 · Camada de acesso a dados**
  - `app/core/db.py` com a sessão/conexão e a dependência do FastAPI.
  - Modelos de cada feature em `models.py`.
  - Fixture de pytest com banco de teste limpo a cada teste.
  - **dep:** T-06

## Fase 2 · Vocabulário

- [ ] **T-09 · Escolher o modelo de embeddings** 🔒
  - Comparar NILC (word2vec/GloVe) e fastText `cc.pt.300` com algumas palavras de teste: os vizinhos fazem sentido?
  - Registrar a escolha e o `model_version` no PRD.

- [ ] **T-10 · Filtros do vocabulário** 🔒 (flexões)
  - Frequência mínima, sem stopwords, sem nomes próprios, sem números, sem termos ofensivos.
  - Decidir e implementar a lematização ("casas" → "casa"), se for adotada.
  - **dep:** T-09

- [ ] **T-11 · `pipeline/build_vocabulary.py`**
  - Lê o arquivo de embeddings, aplica os filtros, normaliza os vetores e grava em `vocabulary` em lotes.
  - Parâmetros: caminho do arquivo, `model_version`, tamanho máximo do vocabulário.
  - Idempotente: rodar de novo com o mesmo `model_version` substitui as linhas.
  - **dep:** T-08, T-10
  - **Pronto quando:** a tabela tem ~50 mil palavras e uma consulta de vizinhos (`ORDER BY embedding <=> ...`) devolve resultados coerentes.

## Fase 3 · Autenticação e papéis

- [ ] **T-12 · Login com Google**
  - `GET /auth/login` redireciona para o OAuth do Supabase.
  - `GET /auth/callback` troca o código pela sessão e grava o token num cookie HTTP-only, `Secure`, `SameSite=Lax`.
  - `POST /auth/logout` apaga o cookie.
  - Renovação do token (refresh) quando expirar.
  - **dep:** T-04, T-05

- [ ] **T-13 · `current_user` e perfil**
  - `app/core/security.py`: dependência `current_user` que valida o JWT do cookie e devolve o perfil (ou `None` para visitantes).
  - No primeiro login, criar `profiles` com nome, avatar e e-mail do Google.
  - Se o e-mail estiver em `ADMIN_EMAILS`, marcar `is_admin = true`.
  - **dep:** T-08, T-12

- [ ] **T-14 · Guardas de permissão**
  - `require_admin` e `require_game_role("player" | "watcher")`, devolvendo 401 (não logado) ou 403 (sem permissão).
  - **dep:** T-13, T-16
  - **Pronto quando:** testes cobrem visitante, player, watcher e admin em cada guarda.

- [ ] **T-15 · Associação ao jogo ativo**
  - No callback do login e ao abrir `/`, criar `game_members` como `player` no jogo ativo, se ainda não existir.
  - Não associar admins nem criar nada sem jogo ativo.
  - **dep:** T-13, T-16

## Fase 4 · Jogos

- [ ] **T-16 · Ciclo de vida do jogo**
  - `games/service.py`: `current_game()` com as transições preguiçosas do RF-7:
    - ativo vencido (`ends_at` passou) → `finished` sem vencedor;
    - sem ativo e agendado com `starts_at` alcançado → `locked`.
  - `unlock_game()`: `locked` → `open`, gravando `unlocked_at` e `unlocked_by`.
  - `finish_game()`: encerramento com o `UPDATE ... WHERE status = 'open' RETURNING` (atômico).
  - As transições devem ser seguras sob concorrência (duas requisições ao mesmo tempo não ativam dois jogos).
  - **dep:** T-08
  - **Pronto quando:** há testes para cada transição, incluindo o fim por data com o jogo ainda bloqueado.

- [ ] **T-17 · Cálculo do ranking do jogo**
  - `build_ranking(game_id)`: apaga as linhas antigas e roda o `INSERT ... SELECT ... ROW_NUMBER()` do RF-8; grava `vocabulary_size`.
  - Validar que a palavra secreta existe no vocabulário.
  - **dep:** T-11, T-16
  - **Pronto quando:** a palavra secreta fica na posição 1 e o total de linhas é igual ao tamanho do vocabulário.

- [ ] **T-18 · Página principal**
  - `GET /` mostra o estado atual:
    - jogo **bloqueado**: aviso "O jogo ainda não foi liberado";
    - jogo **aberto**: campo de palpite (conforme o papel), ranking e mural;
    - **sem jogo ativo**: último jogo encerrado e data do próximo, se houver.
  - Watchers e admins veem a palavra secreta fixada no topo.
  - **dep:** T-05, T-15, T-16

- [ ] **T-19 · Página de jogo encerrado**
  - `GET /games/{id}`: palavra revelada, vencedor (ou "sem vencedor"), ranking final e mural.
  - Devolver 404 para jogos ainda não encerrados (para não vazar nada).
  - **dep:** T-16

## Fase 5 · Palpites e ranking

- [ ] **T-20 · Registrar palpite**
  - `POST /guesses` (player, jogo aberto): normaliza a entrada, consulta `word_rankings` e grava em `guesses` com `guess_date` no fuso de São Paulo.
  - Tratar cada resposta do RF-2: aceito, palavra desconhecida, já chutou hoje, palavra repetida, jogo encerrado, acertou.
  - Erros de vocabulário e repetição **não** consomem o palpite do dia.
  - No acerto: chamar `finish_game()` e, se ele encerrou o jogo, chamar `prizes` (T-26).
  - **dep:** T-14, T-17
  - **Pronto quando:** há testes para cada resposta e para dois acertos simultâneos (só um vence).

- [ ] **T-21 · Ranking coletivo**
  - `GET /ranking` (parcial HTMX): palavras distintas do jogo, ordenadas pela posição, com a contagem de pessoas e os palpites do usuário destacados.
  - Jogo encerrado: palavra secreta e vencedor no topo.
  - Polling HTMX a cada 30 s; após um palpite, a resposta atualiza o ranking na hora.
  - **dep:** T-20

- [ ] **T-22 · Cores e barra de proximidade**
  - `static/js/colors.js` com `rankToColor(rank, total)` em escala logarítmica e tons pastéis.
  - Barra de proximidade proporcional; a posição sempre aparece como número.
  - **dep:** T-21

- [ ] **T-23 · Estados do campo de palpite**
  - Desabilitado com mensagem quando: jogo bloqueado, usuário não é player, já chutou hoje (com contagem regressiva até a virada) ou jogo encerrado.
  - Botão "Entrar para jogar" para visitantes.
  - **dep:** T-20

## Fase 6 · Mural de dicas

- [ ] **T-24 · Publicar e listar dicas**
  - `GET /hints` (parcial HTMX, mais recentes primeiro, com polling).
  - `POST /hints` (watcher, jogo aberto): limite de 280 caracteres; rejeitar textos que contenham a palavra secreta (sem acentos e sem diferença de maiúsculas).
  - **dep:** T-14, T-16

- [ ] **T-25 · Apagar dicas**
  - `DELETE /hints/{id}`: o autor (watcher) ou um admin; preenche `deleted_at` e `deleted_by`.
  - **dep:** T-24

## Fase 7 · Prêmio

- [ ] **T-26 · Atribuição do cupom** 🔒 (origem dos cupons)
  - `prizes/service.py`: gera ou atribui o código ao vencedor, chamado só depois que `finish_game()` confirmou o encerramento.
  - Idempotente: um cupom por jogo (`UNIQUE (game_id)`).
  - `GET /prizes/me`: devolve o cupom do vencedor.
  - **dep:** T-16

- [ ] **T-27 · Modal de vitória e cupom de rasgar**
  - Modal com parabéns, palavra secreta e o cupom: borda tracejada, recortes semicirculares e linha picotada.
  - Animação de "rasgar" por clique ou arrasto (mouse e toque), revelando o código; botão "Copiar código".
  - Link "Meu prêmio" para reabrir o cupom.
  - No celular, o modal ocupa a tela inteira.
  - **dep:** T-20, T-26

## Fase 8 · Painel do admin

- [ ] **T-28 · Lista e criação de jogos**
  - `GET /admin`: jogos com estado, datas, palavra, membros, palpites e vencedor.
  - `GET/POST /admin/games/new`: palavra (precisa existir no vocabulário), `starts_at` e `ends_at`; recusar sobreposição de períodos; calcular o ranking ao salvar.
  - **dep:** T-14, T-17

- [ ] **T-29 · Edição, liberação e encerramento**
  - `GET/POST /admin/games/{id}`: regras de edição por estado (agendado: tudo; ativo: só `ends_at`; encerrado: leitura). Trocar a palavra recalcula o ranking.
  - `POST /admin/games/{id}/unlock`: confirmação com o número de players e watchers.
  - `POST /admin/games/{id}/finish` e `DELETE /admin/games/{id}` (só agendado).
  - **dep:** T-28

- [ ] **T-30 · Membros e watchers**
  - `GET /admin/games/{id}/members`: membros com papel e número de palpites.
  - `POST /admin/games/{id}/members/{user_id}/role`: player → watcher, só se ele não tiver palpites. Nunca watcher → player.
  - **dep:** T-15, T-28

- [ ] **T-31 · Gestão de admins**
  - `GET/POST /admin/users`: lista de usuários; promover ou remover admin (não pode remover o próprio).
  - **dep:** T-28

- [ ] **T-32 · Moderação do mural**
  - Botão de apagar dica visível para admins na página do jogo e no painel.
  - **dep:** T-25, T-28

## Fase 9 · Qualidade e entrega

- [ ] **T-33 · Responsividade**
  - Desktop: ranking e mural em duas colunas. Tablet: colunas estreitas ou mural abaixo. Celular: abas "Ranking" / "Dicas".
  - Painel do admin com tabelas que viram cartões no celular.
  - Áreas de toque ≥ 44 px; testar a 360 px de largura.
  - **dep:** T-21, T-24, T-27, T-28

- [ ] **T-34 · Rate limit**
  - Limitar `POST /guesses` e `POST /hints` por usuário e por IP (por exemplo, com slowapi).
  - **dep:** T-20, T-24

- [ ] **T-35 · Acessibilidade**
  - Contraste das cores pastéis com o texto, foco visível, rótulos nos campos, modal com foco preso e fechamento por Esc.
  - **dep:** T-33

- [ ] **T-36 · Testes de ponta a ponta**
  - Fluxo completo: admin cria jogo → jogo fica bloqueado → usuários entram → admin define watchers e libera → palpites ao longo de dias → acerto → cupom → jogo encerrado visível.
  - Simular a virada do dia sem esperar 24 h (relógio injetável).
  - **dep:** T-27, T-30

- [ ] **T-37 · Deploy**
  - Projeto Supabase de produção (pgvector, provedor Google, RLS da T-07).
  - Hospedagem do FastAPI (por exemplo, Fly.io, Render ou Railway), variáveis de ambiente, HTTPS.
  - Rodar as migrations e o `build_vocabulary.py` contra o banco de produção.
  - **dep:** T-36

## Questões do PRD que bloqueiam tarefas

| Questão | Tarefas bloqueadas |
|---|---|
| Qual modelo de embeddings usar | T-09, T-10, T-11 |
| Flexões ("casas" → "casa") | T-10 |
| Origem dos cupons | T-26 |
| Entre jogos: o próximo começa logo ou espera o `starts_at`? | T-16 (comportamento após o fim antecipado) |
