# Damas Real — Arena

Bem-vindo ao **Damas Real**, uma plataforma web competitiva de jogo de damas que integra funcionalidades de apostas, autenticação social e partidas em tempo real.

## 📋 Descrição do Projeto

O Damas Real é uma aplicação de jogo de damas online desenvolvida para proporcionar uma experiência de arena competitiva. O projeto permite que utilizadores se autentiquem com Google, participem em partidas apostadas e joguem em tempo real através de WebSockets.

### Principais Funcionalidades:
- **Autenticação via Google:** Login seguro e integrado para gestão de perfis.
- **Partidas em Tempo Real:** Utilização de WebSockets para movimentos síncronos e chat durante a partida.
- **Sistema de Apostas:** Integração com sistema financeiro para partidas com valor de aposta (simulado/integrado).
- **IA de Jogo:** Motor interno para validação de movimentos e jogo contra o computador.
- **Interface Responsiva:** Design moderno focado em dispositivos móveis e desktop, utilizando CSS Grid/Flexbox e temas escuros.

## 🛠 Tecnologias Utilizadas

### Backend
- **Python (FastAPI):** Framework web assíncrono de alto desempenho.
- **WebSockets:** Para comunicação bidirecional em tempo real.
- **Supabase:** Base de dados e autenticação escalável.
- **Mercado Pago API:** Integração para processamento de pagamentos.
- **Uvicorn:** Servidor ASGI para rodar a aplicação Python.

### Frontend
- **HTML5/CSS3:** Estrutura e estilização com temas escuros e design responsivo.
- **JavaScript (Vanilla):** Lógica do jogo, manipulação do DOM e gestão de estado global.
- **Google Sign-In:** Integração nativa para autenticação.

## 🚀 Como Executar

### Pré-requisitos
- Python 3.10+
- Conta no [Supabase](https://supabase.com/)
- Conta no [Render](https://render.com/) (para deploy) ou ambiente local.

### Configuração Local
1. Clone o repositório.
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
Crie um arquivo .env na raiz do projeto com as seguintes variáveis:

Snippet de código
GOOGLE_CLIENT_ID=seu_client_id
SUPABASE_URL=sua_url_supabase
SUPABASE_SERVICE_ROLE_KEY=sua_chave_secreta
MP_ACCESS_TOKEN=seu_token_mp
Execute a aplicação:

Bash
python main.py
📁 Estrutura do Projeto
main.py: Código principal do servidor FastAPI e lógica de WebSocket.

index.html: Interface principal.

script.js: Lógica do frontend e comunicação com o backend.

style.css: Estilização e design do sistema.

config.js: Configurações de ambiente (URLs e Client ID).

requirements.txt: Dependências do projeto.

