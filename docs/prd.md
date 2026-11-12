# PRD — Taqui o Contexto

> Status: rascunho · Última atualização: 2026-11-11

## 1. Visão geral

Jogo web de adivinhação de palavras inspirado na mecânica do Contexto: existe uma palavra secreta e cada palpite recebe uma **posição** que indica o quão próxima, em significado, ela está da palavra secreta (1 = acertou).

Diferente do Contexto, o jogo é **coletivo e de longa duração**:

- cada jogo vale até uma **data de fim** definida por um administrador;
- o jogo começa **bloqueado para todos** e só abre quando o admin o libera;
- cada jogador pode dar **um palpite por dia**;
- todos os palpites aparecem num **ranking compartilhado**, então os jogadores se ajudam;
- o **primeiro** a acertar vence, ganha um prêmio (cupom) e o jogo termina para todos;
- **observadores** (watchers) conhecem a palavra, não jogam e deixam dicas num **mural** ao lado do ranking;
- **administradores** gerenciam os jogos e definem quem são os watchers.

## 2. Objetivos

- Criar um hábito diário de retorno (um palpite por dia).
- Gerar colaboração entre jogadores por meio do ranking coletivo e do mural de dicas.
- Recompensar quem acerta primeiro com um cupom resgatável.

### Fora do escopo (v1)

- Placar ou pontuação entre usuários.
- Respostas e reações a dicas (o mural é uma lista simples).
- Aplicativo mobile nativo (a web é responsiva, ver RNF).
- Múltiplos idiomas (o jogo é em português).

## 3. Papéis

Existem dois níveis de papel:

- **Admin** é um papel **global** do usuário (`profiles.is_admin`).
- **Player** e **watcher** são papéis **por jogo**: o mesmo usuário pode ser watcher num jogo e player no seguinte (`game_members.role`).

| Papel | Ver ranking e mural | Ver a palavra secreta | Dar palpites | Escrever no mural | Gerenciar jogos |
|---|---|---|---|---|---|
| **Visitante** (não logado) | ✅ | só após o fim | ❌ | ❌ | ❌ |
| **Player** | ✅ | só após o fim | ✅ (1 por dia) | ❌ | ❌ |
| **Watcher** | ✅ | ✅ | ❌ | ✅ | ❌ |
| **Admin** | ✅ | ✅ | ❌ | ✅ (moderação) | ✅ |

- As permissões são verificadas **no servidor**: um watcher que chame `POST /guesses` recebe 403, um player que chame `POST /hints` também, e qualquer não admin recebe 403 nas rotas `/admin`.
- O primeiro admin é definido por configuração (variável de ambiente `ADMIN_EMAILS`). Depois disso, admins podem promover outros usuários.

### Associação ao jogo

- Quando o usuário **faz login**, ele é associado ao **jogo ativo** como **player**, caso ainda não tenha associação com esse jogo.
- Para cobrir quem já estava logado quando um novo jogo começou, a mesma verificação roda ao abrir a página principal.
- Admins **não** são associados ao jogo: eles conhecem a palavra.
- Se não há jogo ativo, nenhuma associação é criada.
- Um usuário tem **um único papel por jogo**. Isso impede que um watcher, que conhece a palavra, jogue com a mesma conta.

### Designação de watchers

- No painel do jogo, o admin pode transformar um player em watcher, desde que ele **ainda não tenha dado nenhum palpite** naquele jogo.
- O momento natural para isso é enquanto o jogo está **bloqueado** (ver seção 4): as pessoas já fizeram login e foram associadas, mas ninguém pode chutar ainda.
- Um watcher **nunca** volta a ser player no mesmo jogo, porque já viu a palavra.

## 4. Regras do jogo

1. Existe no máximo **um jogo ativo** por vez, com uma palavra secreta.
2. Cada jogo tem `starts_at` e `ends_at`, definidos pelo admin.
3. Um jogo ativo passa por duas fases:
   - **bloqueado** (`locked`): a partir de `starts_at`. Todos podem entrar, ser associados e ver a página, mas **ninguém** pode chutar nem escrever no mural. O admin aproveita para escolher os watchers;
   - **aberto** (`open`): depois que o admin **libera** o jogo. A liberação é única e não pode ser desfeita.
4. Cada player pode registrar **um palpite por dia** no jogo aberto.
   - "Dia" segue o fuso `America/Sao_Paulo`, com virada à meia-noite.
   - Palavras **fora do vocabulário** são rejeitadas e **não consomem** o palpite do dia.
   - Um player não pode repetir uma palavra que ele mesmo já tentou nesse jogo.
5. Cada palpite recebe a sua **posição** no ranking de similaridade com a palavra secreta.
6. O jogo **termina** quando acontece o primeiro destes eventos:
   - **alguém acerta** (posição 1): essa pessoa é a vencedora e ganha o cupom;
   - **chega a data de fim** sem acerto: o jogo termina sem vencedor (inclusive se ainda estiver bloqueado);
   - **o admin encerra** o jogo manualmente.
7. Depois do fim:
   - ninguém pode mais dar palpites nem escrever no mural;
   - a palavra secreta é revelada para todos;
   - o ranking e o mural continuam visíveis, com o vencedor (se houver) em destaque.
8. Se dois palpites corretos chegarem quase ao mesmo tempo, vence o **primeiro registrado** no banco. O segundo recebe a mensagem "o jogo já terminou".

## 5. Requisitos funcionais

### RF-1 · Autenticação e associação

- Login social com **Google** via Supabase Auth.
- No primeiro login, cria-se um perfil (`profiles`) com nome e avatar vindos do Google.
- A cada login, o usuário é associado ao jogo ativo como player, conforme a seção 3.
- Visitantes podem ver o ranking e o mural, mas precisam entrar para interagir.
- Logout disponível no cabeçalho.

### RF-2 · Palpite (player)

- Campo de texto único com botão "Chutar". Habilitado apenas para players num jogo aberto. Com o jogo bloqueado, aparece desabilitado para todos com a mensagem "O jogo ainda não foi liberado".
- A entrada é normalizada: minúsculas, sem espaços nas pontas.
- Respostas possíveis:
  - **aceito:** o palpite entra no ranking com a sua posição;
  - **palavra desconhecida:** mensagem de erro, sem consumir o palpite;
  - **já usou o palpite de hoje:** mensagem com o tempo até a próxima virada;
  - **palavra repetida:** mensagem de erro, sem consumir o palpite;
  - **acertou:** o jogo é encerrado e o modal de vitória abre (RF-5);
  - **jogo encerrado:** mensagem informando que o jogo terminou.
- Depois do palpite do dia, o campo fica desabilitado até a virada.

### RF-3 · Ranking coletivo

- Lista as palavras distintas tentadas no jogo, ordenadas pela posição (mais próxima no topo).
- Cada linha mostra: a palavra, a posição, uma barra de proximidade e quantas pessoas tentaram a palavra.
- Os palpites do usuário logado ficam destacados.
- Watchers e admins veem a palavra secreta fixada no topo, com uma indicação de que só eles a veem.
- Com o jogo encerrado, o topo mostra a palavra secreta e o vencedor (ou "sem vencedor") para todos.
- **Cores:** derivadas da posição, sem armazenamento no banco, e calculadas no servidor ao renderizar o ranking. Tons pastéis, de verde (perto) a vermelho (longe), em escala logarítmica:

  ```python
  def rank_to_color(rank: int, total: int) -> str:
      t = math.log(rank) / math.log(total)  # 0 = perto, 1 = longe
      hue = round(120 * (1 - t))  # 120 = verde, 0 = vermelho
      return f"hsl({hue}, 55%, 82%)"
  ```

- O ranking é atualizado após cada palpite (parcial HTMX) e periodicamente (polling HTMX, por exemplo, a cada 30 s). Assim, todos percebem o fim do jogo sem recarregar a página.

### RF-4 · Mural de dicas (watcher)

- Painel ao lado do ranking com as dicas, da mais recente para a mais antiga.
- Cada dica mostra: o nome e o avatar do autor, o texto e há quanto tempo foi publicada.
- Só watchers veem o campo "Deixar uma dica" e só num jogo aberto.
- Limite de 280 caracteres por dica.
- **Dicas que contêm a palavra secreta são rejeitadas** pelo servidor, comparando sem acentos e sem diferença de maiúsculas.
- O watcher pode apagar as próprias dicas; o admin pode apagar qualquer dica.
- O mural é atualizado por polling HTMX, junto com o ranking.

### RF-5 · Vitória e cupom

- Ao acertar, abre-se para o vencedor um modal com:
  - mensagem de parabéns e a palavra secreta;
  - um **cupom no estilo de sites de cupons**: ticket com borda tracejada, recortes semicirculares nas laterais e linha picotada;
  - interação de **"rasgar"** o canhoto (clique ou arrasto) que revela o código;
  - botão "Copiar código".
- O **admin cadastra o cupom de cada jogo** no painel: o código e, opcionalmente, uma descrição do prêmio (por exemplo, "20% de desconto na loja X"). O mesmo código pode ser usado em mais de um jogo.
- O código é atribuído ao vencedor **no servidor**, na mesma transação que valida o acerto e encerra o jogo. Se o jogo terminar sem cupom cadastrado, o vencedor vê um aviso, e o cupom é entregue assim que o admin cadastrar o código.
- Só o vencedor e os admins veem o código.
- Existe **um cupom por jogo**. Recarregar a página mostra o mesmo cupom ao vencedor.
- O vencedor pode reabrir o cupom depois (por exemplo, pelo link "Meu prêmio").
- Os demais usuários veem apenas o anúncio de quem venceu.

### RF-6 · Painel do admin

**Jogos**

- Listar todos os jogos com estado, datas, palavra secreta, número de membros e de palpites, e o vencedor.
- **Criar jogo:** informar a palavra secreta, `starts_at` e `ends_at`.
  - A palavra precisa existir no vocabulário.
  - Os períodos de jogos diferentes não podem se sobrepor.
  - Ao salvar, o ranking do jogo é calculado no banco (ver RF-8).
- **Editar jogo:**
  - **agendado:** pode mudar palavra secreta (o ranking é recalculado), `starts_at` e `ends_at`;
  - **ativo (bloqueado ou aberto):** pode mudar apenas `ends_at` (a palavra não muda: os watchers já a viram e, depois da abertura, os palpites deixariam de valer);
  - **encerrado:** somente leitura.
- **Liberar** um jogo bloqueado, abrindo-o para palpites e dicas. Antes de confirmar, o painel mostra quantos players e watchers o jogo tem.
- **Encerrar** um jogo ativo manualmente (sem vencedor).
- **Excluir** um jogo agendado.

**Membros e watchers**

- Ver os membros de um jogo, com papel e número de palpites.
- Transformar um player em watcher (só se ele não tiver palpites no jogo).

**Admins**

- Promover um usuário a admin ou remover o papel de admin (não é possível remover o próprio).

**Moderação**

- Apagar qualquer dica do mural.

### RF-7 · Ciclo dos jogos

- Cada jogo tem um estado: `scheduled` (agendado), `locked` (ativo e bloqueado), `open` (ativo e aberto) ou `finished` (encerrado). "Ativo" significa `locked` ou `open`.
- A transição é **preguiçosa**: ao buscar o jogo atual,
  - se o jogo ativo passou do `ends_at`, ele é encerrado sem vencedor;
  - se não há jogo ativo e existe um agendado cujo `starts_at` já chegou, ele passa a `locked`;
  - a passagem de `locked` para `open` acontece **só** pela ação do admin, nunca automaticamente.
- Sem jogo ativo, a página mostra o último jogo encerrado e, se houver, a data de início do próximo.
- Jogos encerrados ficam acessíveis com a palavra revelada, o ranking final e o mural.

### RF-8 · Vocabulário e cálculo do ranking

O trabalho fica dividido em duas etapas:

1. **Offline, uma vez por modelo** (`pipeline/build_vocabulary.py`):
   - monta a lista de palavras a partir das mais frequentes do português (pacote `wordfreq`);
   - filtra o vocabulário (sem stopwords, sem nomes próprios, sem números, sem termos ofensivos, formas lematizadas);
   - gera os vetores com o modelo **`text-embedding-3-large` da OpenAI, reduzido para 1.024 dimensões** (parâmetro `dimensions` da API);
   - grava cada palavra e o seu vetor na tabela `vocabulary` em meia precisão (`halfvec` do **pgvector**, disponível no Supabase), o que dá cerca de 100 MB para 50 mil palavras.

   A OpenAI só é usada nessa etapa, nunca durante o jogo. Gerar o vocabulário inteiro custa centavos.
2. **No banco, a cada jogo criado ou palavra alterada:** a aplicação calcula o ranking com uma única consulta SQL e grava em `word_rankings`:

   ```sql
   INSERT INTO word_rankings (game_id, word, rank)
   SELECT :game_id, v.word,
          ROW_NUMBER() OVER (ORDER BY v.embedding <=> s.embedding)
     FROM vocabulary v,
          (SELECT embedding FROM vocabulary
            WHERE model_version = :model AND word = :secret) s
    WHERE v.model_version = :model;
   ```

   O operador `<=>` é a distância de cosseno do pgvector. Com cerca de 50 mil palavras, a consulta leva poucos segundos.

Assim, o admin cria jogos pelo painel sem rodar scripts, e o servidor web não precisa carregar o modelo nem bibliotecas pesadas.

## 6. Modelo de dados

```
profiles       (id → auth.users.id, display_name, avatar_url, email,
                is_admin BOOLEAN DEFAULT false, created_at)

vocabulary     (model_version, word, embedding HALFVEC(1024))
                 PK (model_version, word)

games          (id, secret_word, model_version, vocabulary_size,
                status ∈ {scheduled, locked, open, finished},
                starts_at, ends_at,
                unlocked_at NULL, unlocked_by → profiles.id NULL,
                winner_id → profiles.id NULL, finished_at NULL,
                prize_code NULL, prize_description NULL,
                created_by → profiles.id, created_at)

game_members   (game_id → games.id, user_id → profiles.id,
                role ∈ {player, watcher} DEFAULT 'player', joined_at)
                 PK (game_id, user_id)                   -- 1 papel por jogo

word_rankings  (game_id → games.id, word, rank)
                 PK (game_id, word)

guesses        (id, user_id → profiles.id, game_id → games.id,
                word, rank, guess_date, created_at)
                 UNIQUE (user_id, game_id, guess_date)   -- 1 palpite por dia
                 UNIQUE (user_id, game_id, word)         -- sem repetir palavra

hints          (id, game_id → games.id, author_id → profiles.id,
                body, created_at, deleted_at NULL, deleted_by NULL)

prizes         (id, game_id → games.id, user_id → profiles.id, code, claimed_at)
                 UNIQUE (game_id)                        -- 1 cupom por jogo
                 -- code é copiado de games.prize_code (cadastrado pelo admin)
```

Decisões:

- **Os vetores ficam só no vocabulário.** Cada palavra tem um vetor guardado uma vez por modelo, em `vocabulary`. Palpites guardam apenas a posição, que é determinística para um par (palavra secreta, modelo) e é o único dado que a interface usa.
- **`word_rankings` é a resposta pré-calculada de cada jogo.** Um palpite vira uma consulta pela chave primária; se a palavra não tem linha, está fora do vocabulário.
- **O limite diário é garantido no banco** (restrição de unicidade), não só na interface.
- **`guess_date` é calculado no servidor** no fuso `America/Sao_Paulo`.
- **O papel por jogo é garantido pela chave primária** de `game_members`: um usuário não pode ser player e watcher no mesmo jogo.
- **O encerramento é atômico.** O acerto encerra o jogo com uma atualização condicional, e só quem conseguiu encerrar é o vencedor:

  ```sql
  UPDATE games
     SET status = 'finished', winner_id = :user_id, finished_at = now()
   WHERE id = :game_id AND status = 'open'
  RETURNING id;   -- sem linha devolvida = alguém venceu antes
  ```

- **Dicas apagadas** são marcadas com `deleted_at` e `deleted_by` em vez de removidas, para facilitar a moderação.

## 7. Arquitetura

- **Backend:** Python + FastAPI.
- **Frontend:** templates Jinja servidos pelo FastAPI + HTMX para as atualizações parciais.
- **Banco e autenticação:** Supabase (Postgres + pgvector + Auth com Google).
- **Embeddings:** OpenAI `text-embedding-3-large` (1.024 dimensões), usado apenas no pipeline offline.
- **Sessão:** o token do Supabase fica num cookie HTTP-only. A dependência `current_user` valida o JWT a cada requisição; `require_admin` e `require_game_role("player" | "watcher")` protegem as rotas.
- O código é escrito em inglês; os textos da interface, em português.

### Organização por feature

```
app/
├── main.py
├── core/        # config, db, security (current_user, require_admin, require_game_role), base.html
├── auth/        # login Google, callback, logout, perfil, associação ao jogo ativo
├── games/       # current_game(), finish_game(), activate_next_game(), build_ranking()
├── guesses/     # registrar palpite, ranking coletivo
├── hints/       # mural de dicas
├── prizes/      # atribuição do cupom, modal de vitória
└── admin/       # painel: jogos, membros, watchers, admins, moderação
pipeline/        # build_vocabulary.py (dependências pesadas, fora do app)
migrations/
```

Dependências entre features:

- `auth` → `games` (associar ao jogo ativo no login);
- `guesses` → `games` (jogo ativo e encerramento) e `guesses` → `prizes` (quando a posição é 1);
- `hints` → `games` (jogo ativo e palavra secreta, para bloquear dicas que a revelam);
- `admin` → `games`, `hints` (orquestra as operações das outras features, sem lógica própria de jogo).

### Rotas

| Método | Rota | Quem | Descrição |
|---|---|---|---|
| GET | `/` | todos | Página principal: jogo atual, ranking e mural |
| GET | `/auth/login` | todos | Redireciona para o OAuth do Google (Supabase) |
| GET | `/auth/callback` | todos | Recebe o retorno do OAuth, grava o cookie e associa ao jogo ativo |
| POST | `/auth/logout` | logado | Encerra a sessão |
| POST | `/guesses` | player | Registra um palpite; devolve o parcial do ranking ou o modal de vitória |
| GET | `/ranking` | todos | Parcial HTMX com o ranking do jogo atual |
| GET | `/hints` | todos | Parcial HTMX com o mural do jogo atual |
| POST | `/hints` | watcher | Publica uma dica |
| DELETE | `/hints/{id}` | watcher (autor), admin | Apaga uma dica |
| GET | `/prizes/me` | vencedor | Cupom do usuário (se houver) |
| GET | `/games/{id}` | todos | Jogo encerrado: palavra revelada, ranking final e mural |
| GET | `/admin` | admin | Lista de jogos |
| GET, POST | `/admin/games/new` | admin | Formulário e criação de jogo |
| GET, POST | `/admin/games/{id}` | admin | Detalhes e edição de um jogo |
| POST | `/admin/games/{id}/unlock` | admin | Libera o jogo para palpites e dicas |
| POST | `/admin/games/{id}/finish` | admin | Encerra o jogo manualmente |
| DELETE | `/admin/games/{id}` | admin | Exclui um jogo agendado |
| GET | `/admin/games/{id}/members` | admin | Membros do jogo e seus papéis |
| POST | `/admin/games/{id}/members/{user_id}/role` | admin | Transforma um player em watcher |
| GET, POST | `/admin/users` | admin | Lista de usuários; promove ou remove admins |

## 8. Requisitos não funcionais

- **Responsividade:**
  - **desktop (≥ 1024 px):** duas colunas, com o ranking à esquerda e o mural de dicas à direita; campo de palpite fixo acima do ranking;
  - **tablet (768–1023 px):** duas colunas mais estreitas, ou o mural abaixo do ranking;
  - **celular (≥ 360 px):** uma coluna com abas "Ranking" e "Dicas"; campo de palpite fixo no topo; o modal do cupom ocupa a tela inteira;
  - o painel do admin também é responsivo; tabelas viram listas de cartões no celular;
  - áreas de toque com pelo menos 44 px; o gesto de rasgar o cupom funciona com o dedo.
- **Segurança:**
  - a palavra secreta e a tabela `word_rankings` só chegam a watchers e admins enquanto o jogo está ativo;
  - toda validação (papel, vocabulário, limite diário, acerto, cupom, conteúdo das dicas) acontece no servidor;
  - rate limit em `POST /guesses` e `POST /hints`;
  - o texto das dicas é escapado na renderização (o Jinja faz isso por padrão);
  - ações do admin ficam registradas (`created_by`, `unlocked_by`, `deleted_by`).
- **Desempenho:** responder a um palpite em menos de 200 ms (consulta por chave primária).
- **Acessibilidade:** a posição é sempre mostrada como número, não só como cor.

## 9. Riscos conhecidos

- **Contas duplicadas:** um watcher pode criar outra conta Google, entrar como player e acertar. O sistema impede isso só na mesma conta. Mitigação na v1: os watchers são pessoas de confiança escolhidas pelo admin, e o admin vê os membros e o histórico de palpites.
- **Vazamento fora do jogo:** um watcher pode contar a palavra a alguém. Não há mitigação técnica; é uma questão de confiança.
- **Dicas indiretas demais:** bloquear a palavra exata não impede dicas como "rima com 'chá'". A moderação do admin cobre esses casos.

## 10. Questões em aberto

- **Entre jogos:** quando um jogo termina antes da data de fim, o próximo começa imediatamente ou espera o seu `starts_at`?
- **Flexões:** palpites como "casas" devem ser convertidos para "casa"? Se sim, precisamos de uma tabela de formas → lema.
