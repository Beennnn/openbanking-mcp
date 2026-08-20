"""bankread — lire ses comptes bancaires, jamais y toucher.

  bankread secrets --set        ranger les identifiants du fournisseur dans le trousseau
  bankread banks [motif]        chercher sa banque dans le catalogue du fournisseur
  bankread link <banque>        signer le consentement (ouvre le site de la banque)
  bankread doctor               ce qui marche, ce qui va casser, quand — sans appel réseau
  bankread accounts             les comptes liés
  bankread balances [--refresh] les soldes, avec leur âge
  bankread tx [--days 30]       les dernières opérations
  bankread upcoming             les échéances détectées et leur prochain passage
  bankread project [--days 45] [--floor 0]
                                solde d'aujourd'hui MOINS ce qui va tomber
  bankread demo                 voir ce que ça donne sur un compte FICTIF, sans banque
  bankread mcp                  serveur MCP sur stdio (pour Claude)

Codes de sortie : 0 tout va bien, 1 il y a quelque chose à regarder, 2 échec dur.
Un barème stable, pour que launchd et les scripts appelants s'y retrouvent.

Python 3.11+, aucune dépendance tierce.
"""

from __future__ import annotations

import argparse
import http.server
import json
import sys
import tempfile
import threading
import urllib.parse
import uuid
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

from . import demo, mcp, read
from .enablebanking import MAX_CONSENT_DAYS as EB_MAX_CONSENT_DAYS
from .erreurs import ApiError
from .gocardless import MAX_CONSENT_DAYS
from .provider import NOMS, charger
from .read import HISTORIQUE_JOURS
from .rs256 import CleInvalide, cle_privee
from .store import SECRETS_ATTENDUS, Store

# Le port de la redirection. Tout fournisseur exige une URL de retour ; pour un usage
# personnel c'est une page locale qui ne vit que le temps du clic. Un port haut et peu
# banal, pour ne rien croiser de ce qui écoute déjà sur la machine.
#
# ⚠ Chez Enable Banking, cette URL doit être déclarée à l'identique dans le portail, à
# la création de l'application (champ « URLs whitelisted for redirecting of end users »).
# Une URL non déclarée fait échouer le parcours au retour, pas au départ.
PORT_RETOUR = 8788
REDIRECT = f"http://127.0.0.1:{PORT_RETOUR}/callback"

PAGE = """<!doctype html><meta charset="utf-8"><title>bankread</title>
<style>body{font:16px/1.6 -apple-system,sans-serif;margin:14vh auto;max-width:32rem;
padding:0 1.5rem;color:#111}code{background:#f2f2f2;padding:.1em .4em;border-radius:4px}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}code{background:#222}}</style>
<h2>%s</h2><p>%s</p><p>Tu peux fermer cet onglet et revenir au terminal.</p>"""


def _store() -> Store:
    return Store()


def _api():
    """Le client du fournisseur configuré.

    `presence_humaine` se déduit du terminal : une commande tapée à la main a un tty,
    l'agent launchd de 7 h 30 n'en a pas. C'est la seule façon honnête de répondre à la
    question que pose l'en-tête PSU — « y a-t-il quelqu'un devant l'écran ? » — sans la
    trancher soi-même dans le sens qui arrange le quota.
    """
    return charger(_store(), presence_humaine=sys.stdin.isatty())


# --------------------------------------------------------------------- secrets

def cmd_secrets(args) -> int:
    store = _store()
    nom = args.provider or store.fournisseur()
    if not args.provider and not store.has_secrets("gocardless") \
            and not store.has_secrets("enablebanking"):
        # Première installation : GoCardless n'accepte plus personne, ne pas envoyer
        # quelqu'un s'inscrire chez un service qui ferme.
        nom = "enablebanking"

    if not args.set:
        present = store.has_secrets(nom)
        print(f"{'✔' if present else '✖'} {NOMS[nom]} : "
              f"{'identifiants présents' if present else 'aucun identifiant (--set)'}")
        return 0 if present else 1

    premier_nom, second_nom = SECRETS_ATTENDUS[nom]
    if nom == "enablebanking":
        print("Application Enable Banking — https://enablebanking.com/sign-in/")
        print("  (Control Panel → API applications ; le .pem se télécharge à la création)")
    else:
        print("Secrets GoCardless — https://bankaccountdata.gocardless.com/user-secrets/")

    # `input()` et non un argument de ligne de commande : un secret passé en argument
    # reste dans l'historique du shell et dans `ps`.
    premier = input(f"  {premier_nom} : ").strip()
    second = input(f"  {second_nom} : ").strip()
    if not premier or not second:
        print("✖ rien saisi, rien enregistré")
        return 2

    if nom == "enablebanking":
        chemin = Path(second).expanduser()
        try:
            second = chemin.read_text()
        except OSError as e:
            print(f"✖ clé privée illisible ({e})")
            return 2
        try:
            cle_privee(second)
        except CleInvalide as e:
            # Refuser tout de suite plutôt que de ranger une clé inutilisable : l'erreur
            # se manifesterait sinon au premier appel réseau, avec un message de la
            # banque qui ne parlerait pas du fichier.
            print(f"✖ ce fichier n'est pas une clé privée RSA lisible : {e}")
            return 2
        print(f"  ✔ clé lue depuis {chemin} — le fichier n'est PAS copié dans le dépôt")

    store.save_secrets(premier, second, fournisseur=nom)
    store.save_fournisseur(nom)
    service = f"bankread-{nom}"
    print(f"✔ rangés dans le trousseau macOS (service {service})")
    if nom == "enablebanking":
        print("  Pense à supprimer le .pem téléchargé : le trousseau en a une copie, "
              "et un fichier finit dans une sauvegarde.")
    return 0


# ---------------------------------------------------------------------- liaison

def cmd_banks(args) -> int:
    """Chercher sa banque. Le catalogue et sa forme dépendent du fournisseur."""
    store = _store()
    if store.fournisseur() == "enablebanking":
        return _banks_enablebanking(args, store)
    return _banks_gocardless(args)


def _banks_gocardless(args) -> int:
    try:
        institutions = _api().institutions(args.country)
    except ApiError as e:
        print(f"✖ {e}")
        return 2
    motif = (args.motif or "").lower()
    trouvees = [i for i in institutions if motif in i.get("name", "").lower()]
    if not trouvees:
        print(f"aucune banque ne contient « {args.motif} » ({len(institutions)} au catalogue)")
        return 1
    largeur = max(len(i["name"]) for i in trouvees)
    for i in sorted(trouvees, key=lambda x: x["name"]):
        jours = i.get("transaction_total_days", "?")
        print(f"  {i['name'].ljust(largeur)}  {i['id']}  (historique {jours} j)")
    return 0


def _banks_enablebanking(args, store) -> int:
    """Chez Enable Banking, une banque n'a pas d'identifiant : elle a un NOM et un pays.

    C'est ce couple qu'attend `link`, au caractère près — d'où l'affichage entre
    guillemets, qui rend visibles les espaces de fin et les accents.
    """
    try:
        banques = _api().aspsps(args.country.upper())
    except ApiError as e:
        print(f"✖ {e}")
        return 2
    motif = (args.motif or "").lower()
    trouvees = [b for b in banques if motif in (b.get("name") or "").lower()]
    if not trouvees:
        print(f"aucune banque ne contient « {args.motif} » ({len(banques)} au catalogue)")
        return 1
    for b in sorted(trouvees, key=lambda x: x.get("name", "")):
        validite = b.get("maximum_consent_validity")
        duree = f"consentement {int(validite) // 86400} j max" if validite else "durée inconnue"
        beta = "  ⚠ bêta" if b.get("beta") else ""
        print(f'  "{b.get("name", "")}"  ({b.get("country", "")})  {duree}{beta}')
    print("\n  bankread link \"<nom exact>\"")
    return 0


def cmd_link(args) -> int:
    """Signer un consentement. La seule commande qui demande un humain devant l'écran.

    Et elle le redemandera dans trois à six mois : la banque plafonne la durée, et rien
    ne peut renouveler à la place de l'utilisateur. `doctor` prévient à J-14.
    """
    store = _store()
    if store.fournisseur() == "enablebanking":
        return _link_enablebanking(args, store)
    return _link_gocardless(args, store)


def _attendre_le_retour(annonce: str) -> dict | None:
    """Ouvre un serveur local le temps d'un clic, et rend les paramètres du retour.

    Le serveur ne vit que pendant le parcours : une page de retour qui resterait à
    l'écoute sur la machine serait une porte ouverte pour rien, et le port choisi est
    haut et peu banal pour ne rien croiser de ce qui écoute déjà.
    """
    recu = threading.Event()
    retour: dict = {}

    class Retour(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            morceaux = urllib.parse.urlparse(self.path)
            if morceaux.path == "/callback":
                retour.update({k: v[0] for k, v in
                               urllib.parse.parse_qs(morceaux.query).items()})
                corps = PAGE % ("Consentement enregistré ✔", annonce)
            else:
                corps = PAGE % ("?", "Page inattendue.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(corps.encode())
            if morceaux.path == "/callback":
                recu.set()

        def log_message(self, *a):  # le serveur ne doit rien dire, la console est à nous
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", PORT_RETOUR), Retour)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    print("  (j'attends le retour — Ctrl-C pour abandonner)")
    try:
        recu.wait(timeout=600)
    except KeyboardInterrupt:
        print("\n✖ abandonné")
        return None
    finally:
        serveur.shutdown()

    if not recu.is_set():
        print("✖ pas de retour après 10 min. Relance `bankread link` quand tu es prêt.")
        return None
    return retour


def _ouvrir(lien: str, no_open: bool) -> None:
    print(f"\n  {lien}\n")
    if no_open:
        print("→ ouvre ce lien à la main, puis identifie-toi.")
    else:
        webbrowser.open(lien)
        print("→ le navigateur s'ouvre ; identifie-toi sur le site de ta banque.")


def _rapatrier_la_fenetre_d_or(store, comptes: list[dict]) -> None:
    """Tirer TOUT l'historique disponible, tout de suite.

    L'historique complet — un à trois ans selon la banque — n'est servi que pendant
    environ une heure après la signature du consentement. Passé ce délai, la banque
    retombe à 90 jours glissants, et une échéance ANNUELLE (taxe foncière, prime
    d'assurance) redevient invisible jusqu'à son prochain passage.

    Cette heure-là ne se rejoue qu'au renouvellement suivant, dans trois à six mois. La
    manquer ne casse rien tout de suite : ça rend juste la projection optimiste pendant
    un an, ce qui est la seule façon de se tromper qui coûte de l'argent. D'où le fait
    que ce soit fait ici, dans la foulée du clic, et pas laissé au brief du lendemain.
    """
    api = _api()
    print("\n  Rapatriement de l'historique complet (la fenêtre se referme dans ~1 h)…")
    for c in comptes:
        nom = c.get("nom") or c.get("id", "")[:8]
        try:
            h = read.transactions(api, store, c["id"], rafraichir=True)
        except Exception as e:  # noqa: BLE001 — un compte qui échoue ne doit pas tuer les autres
            print(f"    {nom} : ✖ {e}")
            continue
        if h.get("souci"):
            print(f"    {nom} : ✖ {h['souci']}")
            continue
        profondeur = h.get("registre_jours", 0)
        assez = "✔" if h.get("annuel_detectable") else "⚠"
        print(f"    {nom} : {h.get('nouvelles_lignes', 0)} lignes fondues, "
              f"registre {profondeur} j {assez}")
    print("  (le registre garde ce que la banque oubliera — ne le purge jamais "
          "avec le cache)")


def _link_gocardless(args, store) -> int:
    api = _api()
    try:
        institutions = api.institutions(args.country)
    except ApiError as e:
        print(f"✖ {e}")
        return 2

    cible = next((i for i in institutions if i["id"] == args.institution), None)
    if cible is None:
        proches = [i for i in institutions if args.institution.lower() in i["name"].lower()]
        if len(proches) == 1:
            cible = proches[0]
        else:
            print(f"✖ « {args.institution} » ne désigne pas une banque unique.")
            for i in proches[:10]:
                print(f"    {i['name']}  →  {i['id']}")
            print("  (bankread banks <motif> pour chercher)")
            return 2

    historique = min(int(cible.get("transaction_total_days", 90) or 90), HISTORIQUE_JOURS)
    print(f"Banque    : {cible['name']}")
    print(f"Historique: {historique} j" + (
        "" if historique >= 380 else
        f"\n            ⚠ moins de 13 mois. Les échéances ANNUELLES (taxe foncière,"
        f"\n              assurances) seront invisibles AU DÉBUT — le registre local les"
        f"\n              accumule ensuite, elles apparaissent à leur deuxième passage."
        f"\n              Les mensuelles, elles, sortent dès {historique} j."))
    print(f"Consentement demandé : {MAX_CONSENT_DAYS} j (maximum), lecture seule")

    try:
        accord = api.create_agreement(cible["id"], historique)
        requisition = api.create_requisition(
            cible["id"], accord["id"], REDIRECT,
            reference=f"bankread-{cible['id']}-{date.today().isoformat()}",
        )
    except ApiError as e:
        print(f"✖ {e}")
        return 2

    _ouvrir(requisition["link"], args.no_open)
    if _attendre_le_retour("bankread peut maintenant lire les soldes et l'historique — "
                           "et rien d'autre.") is None:
        return 2

    try:
        etat = api.requisition(requisition["id"])
        comptes = []
        for aid in etat.get("accounts", []):
            meta = api.account(aid)
            comptes.append({
                "id": aid,
                "iban": meta.get("iban", ""),
                "nom": meta.get("ownerName") or meta.get("name") or "",
                "devise": meta.get("currency", "EUR"),
            })
    except ApiError as e:
        print(f"✖ le consentement est signé mais la relecture a échoué : {e}")
        print("  Relance `bankread link` — le consentement déjà signé sera réutilisé.")
        return 2

    if not comptes:
        print("✖ consentement signé, mais aucun compte partagé. Refais le parcours en "
              "cochant bien les comptes à partager.")
        return 2

    store.add_link(cible["id"], cible["name"], requisition["id"], comptes, MAX_CONSENT_DAYS)
    _annoncer(comptes, MAX_CONSENT_DAYS)
    _rapatrier_la_fenetre_d_or(store, comptes)
    return 0


def _link_enablebanking(args, store) -> int:
    api = _api()
    try:
        banques = api.aspsps(args.country.upper())
    except ApiError as e:
        print(f"✖ {e}")
        return 2

    voulu = args.institution.strip()
    cible = next((b for b in banques if (b.get("name") or "") == voulu), None)
    if cible is None:
        proches = [b for b in banques if voulu.lower() in (b.get("name") or "").lower()]
        if len(proches) == 1:
            cible = proches[0]
        else:
            print(f"✖ « {voulu} » ne désigne pas une banque unique.")
            for b in proches[:10]:
                print(f'    "{b.get("name", "")}"')
            print("  (bankread banks <motif> pour chercher — le nom doit être exact)")
            return 2

    nom, pays = cible.get("name", ""), cible.get("country", args.country.upper())
    validite = cible.get("maximum_consent_validity")
    jours = min(EB_MAX_CONSENT_DAYS, int(validite) // 86400) if validite else EB_MAX_CONSENT_DAYS
    print(f"Banque    : {nom} ({pays})")
    print(f"Consentement demandé : {jours} j (plafond de cette banque), lecture seule")
    print("Historique: ce que la banque voudra bien rendre dans l'heure qui suit — "
          "rapatrié tout de suite")

    # `state` : une valeur à usage unique que la banque nous renvoie telle quelle. On la
    # compare au retour, sinon n'importe quelle page ouverte sur cette machine pourrait
    # appeler notre page de retour avec un code qu'elle a choisi.
    state = uuid.uuid4().hex
    try:
        depart = api.start_auth(nom, pays, REDIRECT, state, jours=jours)
    except ApiError as e:
        print(f"✖ {e}")
        return 2

    _ouvrir(depart.get("url", ""), args.no_open)
    retour = _attendre_le_retour("bankread peut maintenant lire les soldes et "
                                 "l'historique — et rien d'autre.")
    if retour is None:
        return 2
    if retour.get("state") != state:
        print("✖ le retour ne porte pas la marque de CETTE demande — abandon. "
              "Relance `bankread link`.")
        return 2
    code = retour.get("code", "")
    if not code:
        erreur = retour.get("error_description") or retour.get("error") or "sans code"
        print(f"✖ retour sans autorisation ({erreur}).")
        return 2

    try:
        session = api.create_session(code)
    except ApiError as e:
        print(f"✖ le consentement est signé mais l'ouverture de session a échoué : {e}")
        return 2

    comptes = [
        {
            "id": c.get("uid", ""),
            "iban": (c.get("account_id") or {}).get("iban", ""),
            "nom": c.get("name") or c.get("product") or "",
            "devise": c.get("currency", "EUR"),
        }
        for c in session.get("accounts", []) or []
        if c.get("uid")
    ]
    if not comptes:
        print("✖ consentement signé, mais aucun compte partagé. Refais le parcours en "
              "cochant bien les comptes à partager.")
        return 2

    reels = _jours_restants(session) or jours
    store.add_link(nom, nom, session.get("session_id", ""), comptes, reels)
    _annoncer(comptes, reels)
    _rapatrier_la_fenetre_d_or(store, comptes)
    return 0


def _jours_restants(session: dict) -> int | None:
    """Ce que la banque a RÉELLEMENT accordé, pas ce qu'on avait demandé.

    L'écart est courant : on demande six mois, la banque en donne trois. Recopier la
    demande dans l'état ferait annoncer un consentement valide deux mois après sa mort —
    la ligne verte non observée, appliquée à une date.
    """
    fin = ((session.get("access") or {}).get("valid_until") or "").replace("Z", "+00:00")
    try:
        reste = datetime.fromisoformat(fin) - datetime.now(timezone.utc)
    except ValueError:
        return None
    return max(0, round(reste.total_seconds() / 86400))


def _annoncer(comptes: list[dict], jours: int) -> None:
    print(f"\n✔ {len(comptes)} compte(s) lié(s) pour {jours} jours :")
    for c in comptes:
        iban = c.get("iban") or ""
        print(f"    {c.get('nom') or '(sans nom)'}  ····{iban[-4:] if iban else '????'}")


# ------------------------------------------------------------------- lectures

def cmd_doctor(args) -> int:
    s = read.sante(_store())
    print(f"  secrets        {'✔' if s['secrets'] else '✖ absents'}")
    print(f"  comptes liés   {s['comptes_lies']}")
    if s["jeton_refresh_expire_dans_jours"] is not None:
        print(f"  jeton refresh  {s['jeton_refresh_expire_dans_jours']} j restants")
    for kind, q in (s.get("quotas") or {}).items():
        print(f"  quota {kind.ljust(13)} {q.get('remaining', '?')}/{q.get('limit', '?')} restants aujourd'hui")
    for nom, r in (s.get("registres") or {}).items():
        # La banque oublie à 90 jours, le registre non : c'est cette ligne-là qui dit
        # ce que bankread sait VRAIMENT, par opposition à ce que la banque veut bien
        # montrer aujourd'hui.
        if not r["historique_accumule_jours"]:
            # `doctor` ne lit pas le réseau : un registre vide veut dire « rien n'a
            # encore été fondu », PAS « la banque n'a rien ». Annoncer un compte à
            # rebours de 380 j ici serait un chiffre inventé — exactement ce que
            # « aucune ligne verte qui n'ait été observée » interdit, en négatif.
            print(f"  registre {nom[:18].ljust(18)}    — vide : aucune lecture encore "
                  f"fondue (bankread upcoming)")
            continue
        if r["annuel_detectable"]:
            suite = "✔ assez profond pour voir une échéance annuelle"
        else:
            suite = f"annuel visible dans ~{r['jours_restants_avant_annuel']} j"
        print(f"  registre {nom[:18].ljust(18)} {r['historique_accumule_jours']:>4} j accumulés — {suite}")
    for p in s["problemes"]:
        print(f"  ⚠ {p}")
    print(f"\n  {'✔ rien à signaler' if s['verdict'] == 'ok' else '⚠ voir ci-dessus'}")
    return 0 if s["verdict"] == "ok" else 1


def cmd_accounts(args) -> int:
    comptes = read.comptes(_store())
    if not comptes:
        print("✖ aucun compte lié (bankread link)")
        return 1
    for c in comptes:
        reste = c["consentement_jours_restants"]
        marque = " ⚠ à renouveler" if c["consentement_a_renouveler"] else ""
        print(f"  {(c['nom'] or '(sans nom)').ljust(28)} ····{c['iban_fin']}  "
              f"{c['banque']}  — consentement {reste} j{marque}")
        print(f"      {c['id']}")
    return 0


def cmd_balances(args) -> int:
    lignes = read.soldes(_api(), _store(), args.account, rafraichir=args.refresh)
    if not lignes:
        print("✖ aucun compte lié (bankread link)")
        return 1
    code = 0
    for l in lignes:
        if l["etat"] == "inconnu":
            print(f"  ? {(l['nom'] or l['compte'][:8]).ljust(28)} solde inconnu — "
                  f"{l['souci'] or 'jamais lu'}")
            code = 1
            continue
        marque = "" if l["etat"] == "observe" else f"   (lu {l['age_lisible']})"
        print(f"  {'✔' if l['etat'] == 'observe' else '~'} "
              f"{(l['nom'] or l['compte'][:8]).ljust(28)} "
              f"{l['solde']:>10.2f} {l['devise']}{marque}")
        if l["souci"]:
            print(f"      ⚠ {l['souci']}")
            code = 1
    return code


def cmd_tx(args) -> int:
    h = read.transactions(_api(), _store(), args.account, jours=args.days)
    if h["etat"] == "inconnu":
        print(f"  ? historique inconnu — {h.get('souci') or 'jamais lu'}")
        return 1
    for t in h["transactions"][:args.limit]:
        print(f"  {t['date']}  {t['montant']:>10.2f}  {t['libelle'][:56]}")
    print(f"\n  {len(h['transactions'])} opérations sur {args.days} j "
          f"({h['etat']}, {h['age_lisible']})")
    print(f"  registre : {h['registre_jours']} j accumulés"
          + (f", +{h['nouvelles_lignes']} ligne(s) à cette lecture" if h["nouvelles_lignes"] else ""))
    return 0


def cmd_upcoming(args) -> int:
    e = read.echeances(_api(), _store(), args.account)
    if e["etat"] == "inconnu":
        print(f"  ? rien à analyser — {e.get('souci') or 'historique jamais lu'}")
        return 1
    if not e["annuel_detectable"]:
        print(f"  ⚠ {e['historique_jours']} j d'historique accumulé : une échéance ANNUELLE "
              f"(taxe foncière, assurance) n'a pas encore pu être vue passer deux fois,\n"
              f"    donc son absence ne prouve rien et la projection est OPTIMISTE. "
              f"Encore ~{e['jours_avant_annuel_detectable']} j de briefs quotidiens\n"
              f"    et le registre sera assez profond.\n")
    for x in e["echeances"]:
        if not args.all and x["confidence"] != "sure":
            continue
        doute = "" if x["confidence"] == "sure" else "  ? peut-être une coïncidence"
        fam = f"  [{x['famille']}]" if x["famille"] else ""
        print(f"  {x['prochaine']} ±{x['incertitude_jours']}j  {x['montant']:>10.2f}  "
              f"{x['libelle'][:34].ljust(34)} {x['cadence']}{fam}{doute}")
    caches = sum(1 for x in e["echeances"] if x["confidence"] != "sure")
    if caches and not args.all:
        print(f"\n  ({caches} motif(s) vus 2 fois seulement, masqués — `--all` pour les voir)")
    return 0


def cmd_project(args) -> int:
    p = read.projection(_api(), _store(), args.account, jours=args.days, plancher=args.floor)
    return _afficher_projection(p, args.floor)


def _afficher_projection(p: dict, plancher: float) -> int:
    """L'affichage, partagé par `project` et `demo` — même code, mêmes chiffres.

    Si la démonstration avait son propre affichage, elle montrerait autre chose que
    l'outil. Ce serait une brochure, pas un essai.
    """
    if p.get("etat") == "inconnu":
        print(f"  ? pas de projection — {p.get('souci')}")
        return 1

    print(f"  {p['nom'] or p['compte'][:8]} — {p['solde_depart']:.2f} € "
          f"(observé {p['solde_observe_il_y_a']})")
    if not p.get("annuel_detectable", True):
        print(f"  ⚠ historique de {p.get('historique_jours')} j : les échéances annuelles "
              f"manquent à l'appel, la projection est donc OPTIMISTE.")
    print()
    for m in p["mouvements"]:
        fam = f"  [{m['famille']}]" if m["famille"] else ""
        print(f"    {m['date']} ±{m['incertitude_jours']}j  {m['montant']:>9.2f}  →  "
              f"{m['solde_apres']:>9.2f}   {m['libelle'][:30]}{fam}")

    print()
    if p["franchissement"]:
        f = p["franchissement"]
        print(f"  ⚠ passe sous {plancher:.0f} € le {f['date']} "
              f"({f['solde']:.2f} €), poussé par « {f['declencheur']} »")
    else:
        print(f"  ✔ reste au-dessus de {plancher:.0f} € sur {p['fenetre_jours']} j "
              f"(point bas {p['point_bas']['solde']:.2f} € le {p['point_bas']['date']})")
    if p.get("echeances_ignorees_car_incertaines"):
        print(f"    ({p['echeances_ignorees_car_incertaines']} motif(s) incertain(s) "
              f"non comptés — la vraie trajectoire peut être plus basse)")
    return 1 if p["franchissement"] else 0


def cmd_demo(args) -> int:
    """Voir ce que fait l'outil sans avoir de banque branchée.

    Tout est faux SAUF le calcul : le compte, les 400 jours d'historique et le solde sont
    fabriqués, mais la détection d'échéances et la projection qui tournent dessus sont
    exactement celles d'un vrai compte. C'est une démonstration qui est aussi un essai.

    Le magasin est un dossier temporaire, supprimé en sortant : rien de tout ceci ne peut
    atterrir dans le vrai registre, où une ligne inventée resterait pour de bon.
    """
    with tempfile.TemporaryDirectory(prefix="bankread-demo-") as tmp:
        racine = Path(tmp)
        store = Store(config_dir=racine / "config", cache_dir=racine / "cache",
                      data_dir=racine / "data")
        compte = demo.monter(store)

        print("  ┌─ DONNÉES FICTIVES ─────────────────────────────────────────────┐")
        print("  │ Compte inventé, historique fabriqué, solde imaginaire.         │")
        print("  │ Seuls la détection et le calcul sont réels.                    │")
        print("  └────────────────────────────────────────────────────────────────┘")
        print()

        e = read.echeances(None, store, compte)
        print(f"  Échéances retrouvées dans {e.get('historique_jours', 0)} j d'historique :")
        for ech in e.get("echeances", []):
            sur = "" if ech["confidence"] != "faible" else "   ← vue 2 fois : pas crue"
            print(f"    {ech['prochaine']}  {ech['montant']:>9.2f}  "
                  f"{ech['libelle'][:34].ljust(34)}{ech['cadence'][:13].ljust(13)}{sur}")
        print()

        p = read.projection(None, store, compte, jours=args.days, plancher=args.floor)
        code = _afficher_projection(p, args.floor)
        print()
        print("  C'est la soustraction que personne d'autre ne fait : ta banque ne connaît")
        print("  pas tes impôts, et les impôts ne connaissent pas ton solde.")
        return code


def cmd_json(args) -> int:
    """Tout d'un coup, en JSON — ce que le brief du matin consomme."""
    store, api = _store(), _api()
    sortie = {
        "sante": read.sante(store),
        "soldes": read.soldes(api, store, rafraichir=args.refresh),
        "projections": [
            read.projection(None, store, c["id"], jours=args.days, plancher=args.floor)
            for c in read.comptes(store)
        ],
    }
    print(json.dumps(sortie, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bankread", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("secrets", help="ranger/vérifier les identifiants du fournisseur")
    s.add_argument("--provider", choices=sorted(NOMS), default=None,
                   help="fournisseur visé (par défaut : celui qui est configuré)")
    s.add_argument("--set", action="store_true")
    s.set_defaults(func=cmd_secrets)

    s = sub.add_parser("banks", help="chercher une banque")
    s.add_argument("motif", nargs="?", default="")
    s.add_argument("--country", default="fr")
    s.set_defaults(func=cmd_banks)

    s = sub.add_parser("link", help="signer un consentement (3 à 6 mois)")
    s.add_argument("institution")
    s.add_argument("--country", default="fr")
    s.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("doctor", help="diagnostic, sans appel réseau")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("accounts", help="les comptes liés")
    s.set_defaults(func=cmd_accounts)

    s = sub.add_parser("balances", help="les soldes")
    s.add_argument("--account")
    s.add_argument("--refresh", action="store_true", help="forcer un appel (consomme du quota)")
    s.set_defaults(func=cmd_balances)

    s = sub.add_parser("tx", help="les dernières opérations")
    s.add_argument("--account")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_tx)

    s = sub.add_parser("upcoming", help="les échéances détectées")
    s.add_argument("--account")
    s.add_argument("--all", action="store_true", help="montrer aussi les motifs incertains")
    s.set_defaults(func=cmd_upcoming)

    s = sub.add_parser("project", help="solde moins les échéances à venir")
    s.add_argument("--account")
    s.add_argument("--days", type=int, default=45)
    s.add_argument("--floor", type=float, default=0.0)
    s.set_defaults(func=cmd_project)

    s = sub.add_parser("demo", help="voir ce que ça donne, sur un compte fictif")
    s.add_argument("--days", type=int, default=45)
    s.add_argument("--floor", type=float, default=300.0)
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("json", help="tout en JSON (pour le brief)")
    s.add_argument("--days", type=int, default=45)
    s.add_argument("--floor", type=float, default=0.0)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_json)

    s = sub.add_parser("mcp", help="serveur MCP sur stdio")
    s.set_defaults(func=lambda a: mcp.serve())

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except ApiError as e:
        print(f"✖ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
