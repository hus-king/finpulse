"""Public reads of published convertible-bond research snapshots."""
from fastapi import APIRouter

from .cb_research import read_research

router = APIRouter(prefix="/api/convertible-bonds")


@router.get("/research")
def convertible_bond_research():
    return read_research()
