"""Routing contracts, independently of radio, database and language model."""
import pytest

from modules.assistant.response import split_reply
from modules.assistant.router import AssistantRouter, Route


@pytest.mark.parametrize(("question", "route", "reason"), [
    ("Comment tu me reçois ?", Route.TEST, "message_reception"),
    ("Par quels répéteurs est passé mon message ?", Route.PATH, "message_path"),
    ("Which repeaters carried my message?", Route.PATH, "message_path"),
    ("Combien de nœuds actifs aujourd’hui ?", Route.MESH, "network_observation"),
    ("Quels répéteurs n'ont plus été entendus depuis trois jours ?", Route.MESH, "network_observation"),
    ("Comment configurer un répéteur demain ?", Route.WIKI, "documentation"),
    ("Comment fonctionne le chemin d’un message ?", Route.WIKI, "documentation"),
    ("Quelle commande pour configurer la région ?", Route.WIKI, "documentation"),
    ("bonjour", Route.LLM, "conversation"),
    ("llm raconte une histoire", Route.LLM, "explicit"),
    ("mesh: top 5 contacts", Route.MESH, "explicit"),
    ("wiki région", Route.WIKI, "explicit"),
    ("path", Route.PATH, "explicit"),
    ("aide", Route.HELP, "help"),
])
def test_route_contract(question, route, reason):
    decision = AssistantRouter().decide(question)
    assert (decision.route, decision.reason) == (route, reason)


def test_unknown_command_is_not_executable():
    assert AssistantRouter().decide("advert flood").route is Route.WIKI


def test_utf8_pagination_and_disclosed_truncation():
    pages = split_reply("Émetteur très éloigné 📡 " * 40, 140, 3)
    assert len(pages) == 3
    assert all(len(p.encode("utf-8")) <= 140 for p in pages)
    assert pages[-1].endswith("…")


def test_utf8_pagination_preserves_short_answer():
    text = "#BZH: région 868 MHz — vérifier précisément."
    assert split_reply(text, 140) == [text]


@pytest.mark.parametrize("question", [
    "Bonjour, qui es-tu ?", "Salut, présente-toi !", "Hello, who are you?",
    "Raconte une blague courte", "Raconte-moi une histoire de répéteurs",
    "Écris un poème sur la radio", "Please tell me a short joke",
    "Hello, write a story about MeshCore", "Merci !",
])
def test_conversation_does_not_probe_wiki(question):
    assert AssistantRouter().decide(question).route is Route.LLM


@pytest.mark.parametrize(("question", "route"), [
    ("Bonjour, combien de répéteurs actifs ?", Route.MESH),
    ("Bonjour, comment configurer un répéteur ?", Route.WIKI),
    ("wiki raconte une blague courte", Route.WIKI),
    ("mesh qui es-tu ?", Route.MESH),
    ("Comment configurer la radio ?", Route.WIKI),
    ("LoRa spreading factor", Route.WIKI),
])
def test_conversation_rules_preserve_technical_and_explicit_routes(question, route):
    assert AssistantRouter().decide(question).route is route
