"""Pure, ordered routing policy. No DB, HTTP, plugin discovery or RF effects."""
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class Route(str, Enum):
    TEST = "test"
    PATH = "path"
    MESH = "mesh"
    WIKI = "wiki"
    LLM = "llm"
    HELP = "help"


@dataclass(frozen=True)
class Decision:
    route: Route
    question: str
    reason: str


def normalize(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                            if not unicodedata.combining(c)).replace("’", "'").replace("œ", "oe").split())


class AssistantRouter:
    """Conservative French/English intents plus explicit routes for ambiguity.

    Unknown questions reach Wiki lookup first, then general conversation only if
    there is no evidence. Explicit documentary requests never fall back to live
    network data. No forecast rule is triggered by a temporal word alone.
    """

    def decide(self, question: str) -> Decision:
        q = normalize(question).strip(" ?!.")
        first, _, rest = question.strip().partition(" ")
        # Only full route names, no alias registry or arbitrary command execution.
        explicit = {r.value: r for r in Route}
        if first.lower().rstrip(":") in explicit:
            return Decision(explicit[first.lower().rstrip(":")], rest.strip(), "explicit")
        if q in {"", "aide", "help", "aide moi", "que peux-tu faire", "what can you do"}:
            return Decision(Route.HELP, question, "help")
        if q in {"tables", "table", "schema", "db", "base de donnees", "database"}:
            return Decision(Route.MESH, question, "mesh_schema")
        if re.search(r"\b(comment tu me recois|tu me recois|me re[cç]ois|how (?:do|can) you hear me|my (?:snr|rssi)|mon (?:snr|rssi))\b", q):
            return Decision(Route.TEST, question, "message_reception")
        if re.search(r"\b(mon message|my message|mon paquet|my packet)\b", q) and re.search(
            r"\b(chemin|repeteurs?|passe|passe par|path|route|repeaters?|travers|hops?)\b", q
        ):
            return Decision(Route.PATH, question, "message_path")
        # A greeting alone (or followed by an identity question) is not a
        # documentary query. Full matching preserves "bonjour, combien de ...".
        conversational = re.sub(r"^(?:bonjour|salut|bonsoir|hello|hi|hey)[,! .]*", "", q)
        if re.fullmatch(
            r"(?:bonjour|salut|bonsoir|hello|hi|hey|merci|thanks|thank you|"
            r"qui es[- ]tu|tu es qui|presente[- ]toi|who are you|introduce yourself|"
            r"comment vas[- ]tu|ca va|how are you)", q
        ) or (conversational != q and re.fullmatch(
            r"(?:qui es[- ]tu|tu es qui|presente[- ]toi|who are you|introduce yourself|"
            r"comment vas[- ]tu|ca va|how are you)", conversational
        )):
            return Decision(Route.LLM, question, "conversation")
        # Creative requests remain conversation even if they mention radio words
        # such as "courte portee" that can accidentally match the lexical index.
        if re.search(
            r"^(?:(?:bonjour|salut|hello|hi)[,! ]+)?(?:s'il te plait |please )?"
            r"(?:raconte|raconte[- ]moi|ecris|invente|compose|tell|write|make up)\b"
            r".*\b(?:blague|histoire|poeme|chanson|joke|story|poem|song)\b", q
        ):
            return Decision(Route.LLM, question, "conversation")
        if re.search(r"\b(configurer|configuration|parametrer|installer|installation|configure|setup|documentation|wiki|explique|explain)\b", q) or re.search(
            r"\b(comment fonctionne|how does|what is|qu'est.ce|c'est quoi|quelle commande)\b", q
        ):
            return Decision(Route.WIKI, question, "documentation")
        network = re.search(
            r"\b(reseau|mesh|network|noeuds?|nodes?|repeteurs?|repeaters?|contacts?|messages?|"
            r"expediteurs?|senders?|snr|rssi|voisins?|neighbors?|topologie|topology|chemins?|paths?|"
            r"trajets?|routes?|sauts?|hops?|paquets?|packets?|adverts?|annonces?|signal)\b",
            q,
        )
        observation = re.search(
            r"\b(combien|quels?|quelles?|liste|montre|actifs?|active|inactifs?|entendus?|entendu|"
            r"observes?|activite|etat|evolution|meilleur|long|longue|top|how many|which|list|show|"
            r"heard|observed|status|busiest|closest|longest)\b",
            q,
        )
        represented_location = re.search(
            r"\b(pays|countries|villes?|cities)\b.*\b(representes?|presents?|observes?|seen)\b|"
            r"\b(representes?|presents?|observes?|seen)\b.*\b(pays|countries|villes?|cities)\b",
            q,
        )
        if (network and observation) or represented_location:
            return Decision(Route.MESH, question, "network_observation")
        # A probe rather than a documentary assertion: dispatcher may fall back.
        return Decision(Route.WIKI, question, "wiki_probe")
