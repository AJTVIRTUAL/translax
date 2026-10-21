"""
Tests du mode serveur (pré-traduction du site, compatible DeepL) -- SANS
charger de vrai modèle : un moteur factice « traduit » en mettant les mots
en majuscules (repères intacts), ou simule les défauts d'un vrai modèle
(repère perdu, accolade ajoutée) pour vérifier les replis.

Couvre :
  - analyse ICU (variables, pluriels, balises) et aller-retour exact ;
  - masquage / démasquage, découpage en phrases, contrôle des repères ;
  - espaces insécables françaises, guillemets, ß -> ss ;
  - marques, keep_terms, termes imposés (glossary_entries) ;
  - repli par morceaux et avertissements ;
  - serveur HTTP : requêtes JSON et formulaire, CORS (OPTIONS, Private
    Network Access), origine refusée, clé d'accès, en-tête Host, langues,
    usage, santé, glossaires façon DeepL, 503 pendant le chargement ;
  - refus de NLLB (licence CC-BY-NC) à tous les niveaux.

    .venv\\Scripts\\python.exe -m pytest tests/test_server.py -q
    python tests/test_server.py
"""
from __future__ import annotations

import http.client
import json
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import protection as prot  # noqa: E402
from core import server, server_engines, translate  # noqa: E402
from core.translation_service import TranslationService  # noqa: E402

_TAG_SPLIT_RE = re.compile(r"(<[^>]*>|\{[^{}]*\})")


def upper_outside_tags(text: str, target: str) -> str:
    """« Traduction » factice : les mots passent en majuscules, balises et
    variables ({name}, <strong>, <x1/>) sont recopiées telles quelles."""
    return "".join(part if part[:1] in "<{" else part.upper() for part in _TAG_SPLIT_RE.split(text))


class FakeEngine:
    """Moteur factice : même interface que les moteurs de core/server_engines.py."""

    key = "fake"
    license = "Apache-2.0"
    model_id = "test/fake-model"

    def __init__(self, func=upper_outside_tags, loaded: bool = True) -> None:
        self.func = func
        self.calls: list[tuple[list[str], str]] = []
        self.loaded = loaded

    def load(self, on_status=None) -> None:
        self.loaded = True

    def supported_targets(self) -> list[str]:
        return list(server_engines.SERVER_TARGETS)

    def describe(self) -> dict:
        return {"engine": self.key, "label": "Moteur factice", "model": self.model_id, "beams": 1}

    def translate_batch(self, texts: list[str], target: str) -> list[str]:
        self.calls.append((list(texts), target))
        return [self.func(text, target) for text in texts]


def translate_one(text: str, target: str = "de", engine: FakeEngine | None = None, **kwargs):
    service = TranslationService(engine or FakeEngine())
    return service.translate([text], target, **kwargs)[0]


# --------------------------------------------------------------------------
# 1. Analyse ICU


@pytest.mark.parametrize("text", [
    "Merci {name}. Votre paiement de <strong>{amount}</strong> a bien été reçu.",
    "{count, plural, =0 {Aucun service} one {# service} other {# services}}",
    "{minutes} min de lecture · {count, plural, one {# chapitre} other {# chapitres}}",
    "Payez par carte, <twint/> ou QR-facture. <note>Rétractation possible.</note>",
    "{gender, select, female {Elle a <b>{n, number}</b> messages} other {Il a {n, number} messages}}",
    "{n, plural, offset:1 =0 {personne} one {# autre} other {# autres}}",
    "Accolade littérale '{' et apostrophe l''exemple, J'ai un code.",
    "<link>Voir <strong>les conditions</strong></link> · {date, date, short}",
    "",
])
def test_parse_render_round_trip(text):
    assert prot.render_message(prot.parse_message(text)) == text


@pytest.mark.parametrize("text", [
    "Balise <strong>jamais fermée",
    "Fermeture </strong> orpheline",
    "Accolade {name jamais fermée",
    "Accolade } en trop",
    "{count, plural, one {# service} other {# services}",
])
def test_parse_errors_and_lenient_fallback(text):
    with pytest.raises(prot.IcuSyntaxError):
        prot.parse_message(text)
    nodes = prot.parse_lenient(text)
    assert prot.render_message(nodes) == text


def test_plural_structure_and_signature():
    nodes = prot.parse_message("{count, plural, =0 {Aucun service} one {# service} other {# services}}")
    choice = nodes[0]
    assert isinstance(choice, prot.Choice) and choice.kind == "plural"
    assert [b.prefix.strip() for b in choice.branches] == ["=0", "one", "other"]
    assert isinstance(choice.branches[1].children[0], prot.Pound)
    other = prot.parse_message("{count, plural, =0 {Kein Dienst} one {# Dienst} other {# Dienste}}")
    assert prot.structure_signature(nodes) == prot.structure_signature(other)
    moved = prot.parse_message("{count, plural, =0 {Kein Dienst} one {Dienst} other {# Dienste #}}")
    assert prot.structure_signature(nodes) != prot.structure_signature(moved)


# --------------------------------------------------------------------------
# 2. Masquage, découpage, contrôle


def test_mask_and_unmask_identity():
    text = "Merci {name}. Votre paiement de <strong>{amount}</strong> via TWINT a bien été reçu."
    unit = prot.mask_nodes(prot.parse_message(text), prot.TermSet())
    rendered = prot.XML_STYLE.render(unit.canonical, unit)
    assert rendered == "Merci <x1/>. Votre paiement de <g2><x3/></g2> via <x4/> a bien été reçu."
    assert prot.unmask(unit.canonical, unit) == text
    assert prot.XML_STYLE.parse(rendered, unit.canonical, unit) == unit.canonical


def test_source_style_shows_original_syntax_and_reads_it_back():
    text = "Merci {name}, {n, number} fois <strong>{amount}</strong> via <twint/> et STRUKTUR. Le devis."
    unit = prot.mask_nodes(prot.parse_message(text), prot.TermSet(glossary={"devis": "Offerte"}))
    rendered = prot.SOURCE_STYLE.render(unit.canonical, unit)
    assert rendered == "Merci {name}, {n} fois <strong>{amount}</strong> via <twint/> et STRUKTUR. Le <x7/>."
    # Sortie du modèle : ordre changé, mot allemand « Struktur » à côté de la marque.
    output = "Danke {name}: die Struktur von STRUKTUR, <strong>{amount}</strong> {n} Mal mit <twint/>. Die <x7/>."
    canonical = prot.SOURCE_STYLE.parse(output, unit.canonical, unit)
    assert prot.marker_problems(unit.canonical, canonical) == []
    assert prot.unmask(canonical, unit) == (
        "Danke {name}: die Struktur von STRUKTUR, <strong>{amount}</strong> {n, number} Mal mit <twint/>. Die Offerte.")
    # Marque rendue en minuscules par le modèle : casse d'origine rétablie.
    unit = prot.mask_nodes(prot.parse_message("Avec AJT Connect."), prot.TermSet())
    canonical = prot.SOURCE_STYLE.parse("Mit ajt connect.", unit.canonical, unit)
    assert prot.unmask(canonical, unit) == "Mit AJT Connect."


def test_source_style_plural_shown_as_variable_and_pound_kept():
    unit = prot.mask_nodes(prot.parse_message("Voir {count, plural, one {# service} other {# services}}"),
                           prot.TermSet())
    assert prot.SOURCE_STYLE.render(unit.canonical, unit) == "Voir {count}"
    branch = prot.mask_nodes(unit.markers[1].choice.branches[0].children, prot.TermSet())
    assert prot.SOURCE_STYLE.render(branch.canonical, branch) == "# service"


def test_marker_parse_tolerates_model_variants():
    unit = prot.mask_nodes(prot.parse_message("Payez <strong>{amount}</strong> maintenant"), prot.TermSet())
    output = prot.XML_STYLE.parse("Zahlen Sie < g1 ><X2 /></G1> jetzt", unit.canonical, unit)
    assert prot.marker_problems(unit.canonical, output) == []


def test_lost_pairs_and_wrap_span():
    source = "a " + prot.marker("O", 1) + "b c" + prot.marker("C", 1) + " " + prot.marker("A", 2)
    output = "x y z " + prot.marker("A", 2)
    assert prot.lost_pairs(source, output) == [1]
    assert prot.pair_content(source, 1) == "b c"
    assert prot.wrap_span(output, "Y z.", 1) == "x " + prot.marker("O", 1) + "y z" + prot.marker("C", 1) + " " + prot.marker("A", 2)
    assert prot.wrap_span(output, "absent", 1) is None
    assert prot.lost_pairs(source, "x y z") == []  # un élément isolé perdu : pas réparable ainsi


def test_marker_problems_detects_loss_duplicates_crossing_and_moves():
    source = prot.marker("O", 1) + "a " + prot.marker("A", 2) + prot.marker("C", 1) + " b " + prot.marker("A", 3)
    assert prot.marker_problems(source, source) == []
    lost = prot.marker("O", 1) + "a" + prot.marker("C", 1) + prot.marker("A", 3)
    assert any("perdu" in p for p in prot.marker_problems(source, lost))
    double = source + prot.marker("A", 3)
    assert any("double" in p for p in prot.marker_problems(source, double))
    crossed = prot.marker("C", 1) + prot.marker("A", 2) + prot.marker("O", 1) + prot.marker("A", 3)
    assert any("croisées" in p for p in prot.marker_problems(source, crossed))
    moved = prot.marker("O", 1) + "a" + prot.marker("C", 1) + prot.marker("A", 2) + prot.marker("A", 3)
    assert any("sorti" in p for p in prot.marker_problems(source, moved))


def test_split_sentences_respects_tags_abbreviations_and_newlines():
    unit = prot.mask_nodes(prot.parse_message(
        "Première phrase. <strong>Deux. Trois.</strong> M. Dupont arrive. Fin\nLigne deux"), prot.TermSet())
    parts = prot.split_sentences(unit.canonical)
    assert "".join(parts) == unit.canonical
    sentences = [prot.SOURCE_STYLE.render(parts[i], unit) for i in range(1, len(parts), 2)]
    assert sentences == ["Première phrase.", "<strong>Deux. Trois.</strong>", "M. Dupont arrive.", "Fin", "Ligne deux"]


# --------------------------------------------------------------------------
# 3. Typographie, ß, espaces insécables


def test_french_nbsp_removed_before_punctuation():
    result = translate_one("Devise\u00a0: {code}\u202f; vraiment\u00a0? Oui\u00a0!", "de")
    assert result.text == "DEVISE: {code}; VRAIMENT? OUI!"
    assert result.warnings == []


def test_normal_space_before_colon_also_removed_and_kept_for_variables():
    assert translate_one("{legalName} · IDE : {ide}", "it").text == "{legalName} · IDE: {ide}"


def test_guillemets_per_language():
    assert translate_one("«\u00a0Fais ceci.\u00a0»", "de").text == "«FAIS CECI.»"
    assert translate_one("«\u00a0Fais ceci.\u00a0»", "pt").text == "«FAIS CECI.»"
    assert translate_one("«\u00a0Fais ceci.\u00a0»", "en").text == "“FAIS CECI.”"


def test_swiss_german_eszett():
    engine = FakeEngine(func=lambda text, target: text.replace("rue", "Straße").replace("grand", "GROẞ"))
    assert translate_one("La rue est grand", "de", engine=engine).text == "La Strasse est GROSS"
    assert "ß" in translate_one("La rue", "en", engine=engine).text


def test_final_period_added_by_model_is_removed():
    engine = FakeEngine(func=lambda text, target: text + ".")
    assert translate_one("Payer plus tard", "en", engine=engine).text == "Payer plus tard"
    engine = FakeEngine(func=lambda text, target: text)
    assert translate_one("Payer plus tard.", "en", engine=engine).text == "Payer plus tard."


def test_tag_inner_space_kept_and_apostrophe_before_variable():
    text = "Je confirme les conditions.<strong> Le paiement suit.</strong>"
    engine = FakeEngine(func=lambda t, target: t.replace("<strong> ", "<strong>").replace("conditions.", "conditions. "))
    assert translate_one(text, "de", engine=engine).text == text
    engine = FakeEngine(func=lambda t, target: t.replace("suit.</strong>", "suit. </strong>"))
    assert translate_one(text, "de", engine=engine).text == text
    engine = FakeEngine(func=lambda t, target: t.replace("de {brand}", "dell'{brand}"))
    assert translate_one("Le site de {brand}", "it", engine=engine).text == "Le site dell’{brand}"


# --------------------------------------------------------------------------
# 4. Variables, balises, pluriels, termes


def test_variables_and_tags_survive_translation():
    text = "Merci {name}. Votre paiement de <strong>{amount}</strong> a bien été reçu."
    result = translate_one(text, "de")
    assert result.text == "MERCI {name}. VOTRE PAIEMENT DE <strong>{amount}</strong> A BIEN ÉTÉ REÇU."
    assert result.warnings == []


def test_plural_branches_translated_separately_and_pound_kept():
    engine = FakeEngine()
    text = "Voir le panier, {count, plural, =0 {Aucun service} one {# service} other {# services}}"
    result = translate_one(text, "de", engine=engine)
    assert result.text == "VOIR LE PANIER, {count, plural, =0 {AUCUN SERVICE} one {# SERVICE} other {# SERVICES}}"
    assert result.warnings == []
    inputs = engine.calls[0][0]
    assert "Aucun service" in inputs and "# service" in inputs and "# services" in inputs
    assert "Voir le panier, {count}" in inputs


def test_lost_pound_retried_with_a_number():
    # Vu avec MADLAD-400 : « # chapitres » compris comme « nombre de chapitres ».
    answers = {"# chapitre": "# Kapitel", "# chapitres": "Anzahl der Kapitel", "12 chapitres": "12 Kapitel"}
    engine = FakeEngine(func=lambda text, target: answers.get(text, upper_outside_tags(text, target)))
    result = translate_one("{count, plural, one {# chapitre} other {# chapitres}}", "de", engine=engine)
    assert result.text == "{count, plural, one {# Kapitel} other {# Kapitel}}"
    assert result.warnings == []
    assert engine.calls[1][0] == ["12 chapitres"]


def test_pound_number_choice():
    assert prot.pound_number("one", "# service") == "1"
    assert prot.pound_number("other", "# services") == "12"
    assert prot.pound_number("=0", "# service") == "0"
    assert prot.pound_number("offset:1 =2", "# autres") == "2"
    assert prot.pound_number("other", "# offres de 12 mois") == "37"


def test_brands_never_translated():
    def lower(text, target):
        return "".join(p if p[:1] in "<{" else p.lower() for p in _TAG_SPLIT_RE.split(text))

    engine = FakeEngine(func=lower)
    text = "AJT Connect et STRUKTUR, avec AJTASK, TWINT, App Store et Google Play."
    result = translate_one(text, "de", engine=engine)
    assert result.text == text  # casse des marques rétablie après le modèle
    assert result.warnings == []
    assert engine.calls[0][0] == [text]  # montrées en clair au modèle (contexte)


def test_brand_translated_by_model_is_retranslated_masked():
    # Comme « STRUKTUR » -> « STRUTTURA » vu en italien : la phrase entière est
    # retraduite avec la marque en repère neutre, sans repli par morceaux.
    engine = FakeEngine(func=lambda text, target: upper_outside_tags(text.replace("STRUKTUR", "Struttura"), target))
    result = translate_one("AJTASK et STRUKTUR", "it", engine=engine)
    assert result.text == "AJTASK ET STRUKTUR" and result.warnings == []
    assert engine.calls[1][0] == ["<x1/> et <x2/>"]


def test_brand_lost_everywhere_is_reported():
    def drop(text, target):
        return upper_outside_tags(text.replace("TWINT", "").replace("<x1/>", ""), target)

    result = translate_one("Payer avec TWINT maintenant", "de", engine=FakeEngine(func=drop))
    assert any("absent" in w and "TWINT" in w for w in result.warnings)


def test_keep_terms_and_glossary_entries():
    engine = FakeEngine()
    result = translate_one("Le devis Signature Pro est prêt. Devis signé.", "de", engine=engine,
                           keep_terms=["Signature Pro"], glossary={"devis": "Offerte"})
    assert result.text == "LE Offerte Signature Pro EST PRÊT. Offerte SIGNÉ."
    sent = " ".join(" ".join(texts) for texts, _target in engine.calls)
    assert "devis" not in sent.lower()  # le terme imposé n'est jamais montré en français
    assert "<x1/>" in sent and "Signature Pro" in sent


def test_longest_glossary_entry_wins():
    result = translate_one("Prix sur devis, devis gratuit.", "de",
                           glossary={"devis": "Offerte", "sur devis": "auf Anfrage"})
    assert result.text == "PRIX auf Anfrage, Offerte GRATUIT."


def test_glossary_capitalised_at_sentence_start():
    engine = FakeEngine(func=lambda text, target: text.replace("Le ", ""))
    result = translate_one("Le devis arrive.", "en", engine=engine, glossary={"devis": "quote"})
    assert result.text == "Quote arrive."


def test_all_texts_of_a_request_go_to_the_engine_in_one_batch_and_cache():
    engine = FakeEngine()
    service = TranslationService(engine)
    texts = ["Fermer", "Fermer", "Payer maintenant", "Merci {name}.", "Un. Deux."]
    results = service.translate(texts, "de")
    assert [r.text for r in results] == ["FERMER", "FERMER", "PAYER MAINTENANT", "MERCI {name}.", "UN. DEUX."]
    assert len(engine.calls) == 1
    assert sorted(engine.calls[0][0]) == sorted(["Fermer", "Payer maintenant", "Merci {name}.", "Un.", "Deux."])
    service.translate(["Fermer"], "de")
    assert len(engine.calls) == 1  # servi par le cache


# --------------------------------------------------------------------------
# 5. Replis et avertissements


def test_lost_variable_triggers_translation_by_pieces():
    def lossy(text, target):
        out = upper_outside_tags(text, target)
        # Perd les variables quand la phrase entière arrive d'un bloc.
        return re.sub(r"\{\w+\}", "", out) if "<strong>" in text else out

    engine = FakeEngine(func=lossy)
    text = "Merci {name}, votre paiement de <strong>{amount}</strong> est reçu."
    result = translate_one(text, "de", engine=engine)
    assert result.text == "MERCI {name}, VOTRE PAIEMENT DE <strong>{amount}</strong> EST REÇU."
    assert any("morceaux" in w for w in result.warnings)
    assert len(engine.calls) == 2
    assert sorted(engine.calls[1][0]) == ["Merci", "est reçu", "votre paiement de"]


def test_lost_tag_pair_repaired_without_losing_context():
    def drop_strong(text, target):
        # Comme MADLAD sur une phrase réorganisée : la balise disparaît, le contenu reste.
        return upper_outside_tags(text.replace("<strong>", "").replace("</strong>", ""), target)

    engine = FakeEngine(func=drop_strong)
    text = "Le devis constitue un <strong>engagement ferme</strong> pour {name}."
    result = translate_one(text, "de", engine=engine)
    assert result.text == "LE DEVIS CONSTITUE UN <strong>ENGAGEMENT FERME</strong> POUR {name}."
    assert result.warnings == []  # réparé, pas de repli par morceaux
    assert engine.calls[1][0] == ["engagement ferme"]


def test_lost_tag_pair_repaired_by_approximation_with_warning():
    def reword(text, target):
        if "<strong>" in text:  # phrase entière : balise perdue ET contenu reformulé
            text = text.replace("<strong>", "").replace("</strong>", "").replace("ferme", "solide")
        return upper_outside_tags(text, target)

    result = translate_one("Le devis constitue un <strong>engagement contractuel ferme</strong>.", "de",
                           engine=FakeEngine(func=reword))
    assert result.text == "LE DEVIS CONSTITUE UN <strong>ENGAGEMENT CONTRACTUEL</strong> SOLIDE."
    assert any("rapprochement" in w for w in result.warnings)
    assert not any("morceaux" in w for w in result.warnings)


def test_single_renamed_variable_recovered_but_never_guessed_when_ambiguous():
    engine = FakeEngine(func=lambda t, target: upper_outside_tags(t.replace("{number}", "{Nummer}"), target))
    result = translate_one("Commande n° {number} reçue", "de", engine=engine)
    assert result.text == "COMMANDE N° {number} REÇUE" and result.warnings == []

    engine = FakeEngine(func=lambda t, target: upper_outside_tags(t.replace("{a}", "{y}").replace("{b}", "{x}"), target))
    result = translate_one("De {a} à {b} jours", "de", engine=engine)
    assert result.text == "DE {a} À {b} JOURS"
    assert any("morceaux" in w for w in result.warnings)


def test_lost_tag_pair_around_variable_only_repaired_without_model_call():
    engine = FakeEngine(func=lambda text, target: upper_outside_tags(
        text.replace("<strong>", "").replace("</strong>", ""), target))
    result = translate_one("Total : <strong>{amount}</strong>", "de", engine=engine)
    assert result.text == "TOTAL: <strong>{amount}</strong>"
    assert len(engine.calls) == 1


def test_persistent_failure_gives_warning():
    engine = FakeEngine(func=lambda text, target: upper_outside_tags(text, target) + " {oups}")
    result = translate_one("Bonjour {name}, tout va bien.", "de", engine=engine)
    assert any("contrôle final" in w for w in result.warnings)


def test_invalid_icu_is_translated_with_warning():
    result = translate_one("Texte <strong>sans fin et {name}", "de")
    assert "{name}" in result.text and "<strong>" in result.text
    assert any("ICU" in w for w in result.warnings)


def test_lost_glossary_marker_reinserted_inline():
    def drop_marker(text, target):
        return text.replace("<x1/>", "") if "<x1/>" in text else text.replace("Offerte", "OFFERTE")

    result = translate_one("Votre devis arrive", "de", engine=FakeEngine(func=drop_marker),
                           glossary={"devis": "Offerte"})
    assert result.text == "Votre OFFERTE arrive" or result.text == "Votre Offerte arrive"
    assert not any("absent" in w for w in result.warnings)


# --------------------------------------------------------------------------
# 6. Licences : NLLB refusé partout


@pytest.mark.parametrize("key", ["600M", "1.3B", "3.3B", "600M-ct2", "nllb", "facebook/nllb-200-distilled-600M"])
def test_nllb_refused_by_name(key):
    with pytest.raises(server_engines.EngineRefused, match="CC-BY-NC"):
        server_engines.check_engine_allowed(key)
    with pytest.raises(server_engines.EngineRefused):
        server_engines.create_engine(key)


def test_nllb_engine_instance_refused_by_service_and_server():
    engine = translate.PreciseEngine(model_key="600M")  # construit sans charger le modèle
    with pytest.raises(server_engines.EngineRefused, match="CC-BY-NC"):
        TranslationService(engine)
    with pytest.raises(server_engines.EngineRefused):
        server.build_server(engine, port=0)


def test_engine_without_commercial_licence_refused_at_translate_time():
    engine = FakeEngine()
    service = TranslationService(engine)
    engine.license = "CC-BY-NC-4.0"
    with pytest.raises(server_engines.EngineRefused):
        service.translate(["Bonjour"], "de")


def test_cli_refuses_nllb_before_loading_anything(capsys):
    assert server.main(["--engine", "600M", "--port", "0"]) == 2
    assert "CC-BY-NC" in capsys.readouterr().err


def test_allowed_engines():
    assert server_engines.check_engine_allowed("madlad-ct2").license == "Apache-2.0"
    assert server_engines.check_engine_allowed("MADLAD").license == "Apache-2.0"
    assert server_engines.check_engine_allowed("opus-mt").license == "CC-BY-4.0"
    with pytest.raises(server_engines.EngineRefused, match="inconnu"):
        server_engines.check_engine_allowed("google-translate")


# --------------------------------------------------------------------------
# 7. Serveur HTTP


ADMIN = "https://admin.ajtvirtual.com"


@pytest.fixture()
def running(tmp_path):
    """Serveur réel sur un port libre, moteur factice déjà « chargé »."""
    servers = []

    def start(engine=None, **kwargs):
        engine = engine or FakeEngine()
        httpd = server.build_server(engine, port=0, glossary_path=tmp_path / "glossaires.json",
                                    log=lambda message: None, **kwargs)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        servers.append(httpd)
        return httpd, engine

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def request(httpd, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    data = None
    headers = dict(headers or {})
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode("utf-8")
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    content_type = response.getheader("Content-Type") or ""
    payload = json.loads(raw) if raw and "json" in content_type else raw.decode("utf-8")
    return response.status, dict(response.getheaders()), payload


def test_http_translate_json(running):
    httpd, engine = running()
    status, headers, payload = request(httpd, "POST", "/v2/translate", {
        "text": ["Bonjour {name}", "{count, plural, one {# service} other {# services}}"],
        "source_lang": "FR", "target_lang": "DE", "tag_handling": "xml",
        "glossary_entries": {"service": "Leistung"}, "keep_terms": ["Bonjour"],
    })
    assert status == 200
    assert payload == {"translations": [
        {"detected_source_language": "FR", "text": "Bonjour {name}"},
        {"detected_source_language": "FR", "text": "{count, plural, one {# Leistung} other {# SERVICES}}"},
    ]}
    assert engine.calls[0][1] == "de"


def test_http_translate_form_encoded_like_deepl(running):
    httpd, engine = running()
    body = urlencode([("text", "Fermer"), ("text", "Payer\u00a0!"), ("target_lang", "PT-PT"), ("source_lang", "FR")])
    status, _headers, payload = request(httpd, "POST", "/v2/translate", body,
                                        {"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 200
    assert [t["text"] for t in payload["translations"]] == ["FERMER", "PAYER!"]
    assert engine.calls[0][1] == "pt"


def test_http_warnings_field_only_when_a_check_failed(running):
    engine = FakeEngine(func=lambda text, target: upper_outside_tags(text, target) + " {oups}")
    httpd, _engine = running(engine)
    _status, _headers, payload = request(httpd, "POST", "/v2/translate",
                                         {"text": ["Bonjour {name}"], "target_lang": "IT"})
    assert payload["translations"][0]["warnings"]


def test_http_cors_preflight_with_private_network(running):
    httpd, _engine = running()
    status, headers, _payload = request(httpd, "OPTIONS", "/v2/translate", headers={
        "Origin": ADMIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, authorization",
        "Access-Control-Request-Private-Network": "true",
    })
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == ADMIN
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    assert "Content-Type" in headers["Access-Control-Allow-Headers"]
    assert "POST" in headers["Access-Control-Allow-Methods"]


def test_http_cors_headers_on_actual_request_and_localhost_dev(running):
    httpd, _engine = running()
    status, headers, _payload = request(httpd, "POST", "/v2/translate",
                                        {"text": ["Oui"], "target_lang": "EN"},
                                        {"Origin": "http://localhost:3001"})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:3001"


def test_http_foreign_origin_refused_before_any_work(running):
    httpd, engine = running()
    status, headers, _payload = request(httpd, "OPTIONS", "/v2/translate",
                                        headers={"Origin": "https://evil.example"})
    assert status == 403 and "Access-Control-Allow-Origin" not in headers
    status, headers, payload = request(httpd, "POST", "/v2/translate", {"text": ["Oui"], "target_lang": "DE"},
                                       {"Origin": "https://evil.example"})
    assert status == 403 and "Origine non autorisée" in payload["message"]
    assert engine.calls == []


def test_http_configurable_origins(running):
    httpd, _engine = running(cors_origins=["https://autre.example"])
    status, _headers, _payload = request(httpd, "OPTIONS", "/v2/translate", headers={"Origin": ADMIN})
    assert status == 403
    status, headers, _payload = request(httpd, "OPTIONS", "/v2/translate", headers={"Origin": "https://autre.example"})
    assert status == 204 and headers["Access-Control-Allow-Origin"] == "https://autre.example"


def test_http_host_header_checked(running):
    httpd, engine = running()
    status, _headers, payload = request(httpd, "POST", "/v2/translate", {"text": ["Oui"], "target_lang": "DE"},
                                        {"Host": "evil.example:8765"})
    assert status == 403 and "Hôte refusé" in payload["message"]
    assert engine.calls == []


def test_http_auth_key_only_when_configured(running):
    httpd, _engine = running(auth_key="secret-123")
    body = {"text": ["Oui"], "target_lang": "DE"}
    assert request(httpd, "POST", "/v2/translate", body)[0] == 403
    assert request(httpd, "POST", "/v2/translate", body, {"Authorization": "DeepL-Auth-Key faux"})[0] == 403
    assert request(httpd, "POST", "/v2/translate", body, {"Authorization": "DeepL-Auth-Key secret-123"})[0] == 200
    form = urlencode([("text", "Oui"), ("target_lang", "DE"), ("auth_key", "secret-123")])
    assert request(httpd, "POST", "/v2/translate", form,
                   {"Content-Type": "application/x-www-form-urlencoded"})[0] == 200
    status, _headers, health = request(httpd, "GET", "/health")
    assert status == 200 and health["auth_required"] is True
    preflight = request(httpd, "OPTIONS", "/v2/translate", headers={"Origin": ADMIN})
    assert preflight[0] == 204  # le navigateur n'envoie jamais la clé dans un OPTIONS


def test_http_languages_usage_health(running):
    httpd, _engine = running()
    assert request(httpd, "GET", "/v2/languages?type=source")[2] == [{"language": "FR", "name": "French"}]
    targets = [item["language"] for item in request(httpd, "GET", "/v2/languages?type=target")[2]]
    assert {"DE", "IT", "EN-GB", "PT-PT"} <= set(targets)
    request(httpd, "POST", "/v2/translate", {"text": ["Bonjour"], "target_lang": "DE"})
    assert request(httpd, "GET", "/v2/usage")[2]["character_count"] == len("Bonjour")
    health = request(httpd, "GET", "/health")[2]
    assert health["ready"] is True and health["engine"] == "fake" and health["license"] == "Apache-2.0"


def test_http_errors(running):
    httpd, _engine = running()
    assert request(httpd, "POST", "/v2/translate", {"target_lang": "DE"})[0] == 400
    assert request(httpd, "POST", "/v2/translate", {"text": ["x"], "target_lang": "JA"})[0] == 400
    assert request(httpd, "POST", "/v2/translate", {"text": ["x"], "target_lang": "DE", "source_lang": "EN"})[0] == 400
    assert request(httpd, "POST", "/v2/translate", "{pas du json", {"Content-Type": "application/json"})[0] == 400
    too_many = {"text": ["x"] * (server.MAX_TEXTS + 1), "target_lang": "DE"}
    assert request(httpd, "POST", "/v2/translate", too_many)[0] == 413
    assert request(httpd, "GET", "/v2/inconnu")[0] == 404


def test_http_503_while_model_loads(running):
    httpd, engine = running(FakeEngine(loaded=False))
    status, headers, payload = request(httpd, "POST", "/v2/translate", {"text": ["Oui"], "target_lang": "DE"})
    assert status == 503 and headers.get("Retry-After") == "10"
    assert request(httpd, "GET", "/health")[2]["status"] == "loading"
    httpd.state.load_engine()
    assert request(httpd, "POST", "/v2/translate", {"text": ["Oui"], "target_lang": "DE"})[0] == 200


def test_http_target_language_not_offered_by_engine(running):
    engine = FakeEngine()
    engine.supported_targets = lambda: ["de", "en"]
    httpd, _engine = running(engine)
    status, _headers, payload = request(httpd, "POST", "/v2/translate", {"text": ["Oui"], "target_lang": "IT"})
    assert status == 400 and "indisponible" in payload["message"]


def test_http_glossaries_like_deepl(running, tmp_path):
    httpd, engine = running()
    status, _headers, info = request(httpd, "POST", "/v2/glossaries", {
        "name": "Site", "source_lang": "fr", "target_lang": "de",
        "entries": "devis\tOfferte\nprestation\tLeistung\n", "entries_format": "tsv",
    })
    assert status == 201 and info["entry_count"] == 2 and info["ready"] is True
    glossary_id = info["glossary_id"]
    assert request(httpd, "GET", "/v2/glossaries")[2]["glossaries"][0]["glossary_id"] == glossary_id
    assert request(httpd, "GET", f"/v2/glossaries/{glossary_id}")[2]["name"] == "Site"
    assert request(httpd, "GET", f"/v2/glossaries/{glossary_id}/entries")[2] == "devis\tOfferte\nprestation\tLeistung\n"
    assert (tmp_path / "glossaires.json").exists()

    status, _headers, payload = request(httpd, "POST", "/v2/translate", {
        "text": ["Votre devis"], "source_lang": "FR", "target_lang": "DE", "glossary_id": glossary_id,
    })
    assert status == 200 and payload["translations"][0]["text"] == "VOTRE Offerte"
    wrong_language = {"text": ["Votre devis"], "target_lang": "IT", "glossary_id": glossary_id}
    assert request(httpd, "POST", "/v2/translate", wrong_language)[0] == 400

    assert request(httpd, "DELETE", f"/v2/glossaries/{glossary_id}")[0] == 204
    assert request(httpd, "GET", f"/v2/glossaries/{glossary_id}")[0] == 404
    bad = {"name": "X", "source_lang": "fr", "target_lang": "de", "entries": "sans tabulation"}
    assert request(httpd, "POST", "/v2/glossaries", bad)[0] == 400


def test_glossary_entries_formats():
    assert server.coerce_glossary({"devis": "Offerte"}) == {"devis": "Offerte"}
    assert server.coerce_glossary([["devis", "Offerte"]]) == {"devis": "Offerte"}
    assert server.coerce_glossary("devis\tOfferte") == {"devis": "Offerte"}
    assert server.coerce_glossary('{"devis": "Offerte"}') == {"devis": "Offerte"}
    assert server.coerce_terms("STRUKTUR, AJTASK\nTWINT") == ["STRUKTUR", "AJTASK", "TWINT"]


def test_server_listens_on_loopback_only(running):
    httpd, _engine = running()
    assert httpd.server_address[0] == "127.0.0.1"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
