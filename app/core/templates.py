from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, PrefixLoader, select_autoescape

APP_DIR = Path(__file__).resolve().parent.parent
FEATURES = ("core", "auth", "games", "guesses", "hints", "prizes", "admin")

# Each feature keeps its own templates folder; templates are referenced as
# "<feature>/<file>.html", e.g. "core/base.html" or "guesses/ranking.html".
_loader = PrefixLoader(
    {feature: FileSystemLoader(APP_DIR / feature / "templates") for feature in FEATURES}
)

templates = Jinja2Templates(env=Environment(loader=_loader, autoescape=select_autoescape(["html"])))
