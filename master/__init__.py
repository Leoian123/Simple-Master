"""Simple Master: un master di gioco di ruolo minimalista guidato da Claude.

Flusso di un turno:
    giocatore -> MasterEngine -> API (consulta glossario, chiede file) -> risposta.
"""

from . import logbook
from .campaign import Campaign
from .engine import MasterEngine, TurnResult
from .files import CampaignFile
from .glossary import Glossary, GlossaryEntry

__all__ = ["Campaign", "CampaignFile", "Glossary", "GlossaryEntry", "MasterEngine", "TurnResult", "logbook"]
