from flask import Blueprint

supply_chain_bp = Blueprint(
    "supply_chain",
    __name__,
    template_folder="templates",
    url_prefix="/supply-chain"
)

from . import routes  # noqa: E402, F401
