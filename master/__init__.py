"""Simple Master: un master di gioco di ruolo minimalista guidato da Claude.

Flusso di un turno di gioco:
    giocatore -> MasterEngine (contesto composto dal codice) -> narratore -> scriba -> risposta.
Il costruttore di schede usa invece il ciclo a conversazione di ConversationEngine.
"""

from . import logbook
from .campaign import Campaign
from .conversation import ConversationEngine, TurnResult
from .engine import MasterEngine
from .files import CampaignFile
from .glossary import Glossary, GlossaryEntry

__all__ = ["Campaign", "CampaignFile", "ConversationEngine", "Glossary", "GlossaryEntry", "MasterEngine", "TurnResult", "logbook"]
