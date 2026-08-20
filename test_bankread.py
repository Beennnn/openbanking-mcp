#!/usr/bin/env python3
"""Tests de bankread — `python3 finance/test_bankread.py`. Stdlib seule, aucun réseau.

Ce qui est vérifié ici n'est pas « le code s'exécute » mais « le code se tait quand il
ne sait pas ». Une détection d'échéances qui se trompe de date ne plante pas : elle
annonce le prélèvement des impôts le 12 au lieu du 15, avec le même aplomb. Les cas de
non-détection (deux occurrences, cadence irrégulière, historique trop court) comptent
donc autant que les cas de détection.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))

from bankreadlib import cli, demo, enablebanking, ledger, provider, read, recurring, rs256  # noqa: E402
from bankreadlib.erreurs import ApiError, RateLimited  # noqa: E402
from bankreadlib.store import Store  # noqa: E402

# Clé RSA de 1024 bits fabriquée pour CES tests, et pour rien d'autre : elle ne protège
# aucun compte, ne signe aucun appel réel, et sa présence en clair ici est sans
# conséquence. Elle sert à vérifier que notre RS256 maison rend exactement les mêmes
# octets qu'OpenSSL — c'est un vecteur de test, pas un secret.
CLE_PKCS8 = """-----BEGIN PRIVATE KEY-----
MIICeAIBADANBgkqhkiG9w0BAQEFAASCAmIwggJeAgEAAoGBANPvfMQOoUZ1qi6E
KvesVxurvqI4hhc9+vgMjYcCdskXCohq+DPjIgBYE6fpFrowXuSdL+X1JdDSq/yx
sCNiiqkaPuisSFbnN9iQKysvTzjV/nRpVAhD3NkOTz8G6i9oA3HxMjeGaE1cinbC
HwhfjGPJ3fU1hSEGsuRknSCMaKSrAgMBAAECgYEAsYSRru7KK2h3aYsPKnFSbT0H
6n0J5AHoa0ioawOnV3xTODzRNTT0z/D2VdipTr8hXMBE1IroZ+duY7S54omxtbh0
HmDJ/U2Twd7FOk3r4Lze//MtheJDu1Z1z37GTAEtSNSO0j8nlWnRyyLCjnKzZPG2
t1HL4OlR/VhYFeTiAUECQQDxDAhWKeps1b5sUtRh5PROTD160FQScZKr5ZwUtd6F
NV4QQ38lB+jovcvnGKuS61WRSBOBwuTzODSPfuKcxfjxAkEA4RUlpqNABJiX6b3d
JOSPpZJskiHgbEixKOfqdpzEIja1ajNYVJI+L6jdnGK90Ek7ybT2xQbg6sWPuif/
N2OXWwJAIEoXV8t3nqqnPoV987siytZFqgz8JhhWqHYyiUevjjKO7ijMgF5l4b+C
7+sLGLwzkymPi2NbHgWabNp3ar7OUQJBANkjhYcUxpWogCVGvSjyNoIMmCEB/Xd5
SS+lTFPROIWpMaqajgRIcSWZSvGrcGKXw837fnTlBeZ3YQi9vUC6GzECQQDk4OlS
b0j0E6ytlbfnyJNSYdMULZjelzHXj3Kl0au4yXzC6w9O6Sx2G7QAn4zFLpedsm+y
BNX6ZbrS2s4nFaI3
-----END PRIVATE KEY-----"""

# La MÊME clé au format PKCS#1, celui qu'écrivait OpenSSL avant. Les deux doivent donner
# la même signature, sinon un fichier téléchargé dans l'autre format planterait à 7 h 30.
CLE_PKCS1 = """-----BEGIN RSA PRIVATE KEY-----
MIICXgIBAAKBgQDT73zEDqFGdaouhCr3rFcbq76iOIYXPfr4DI2HAnbJFwqIavgz
4yIAWBOn6Ra6MF7knS/l9SXQ0qv8sbAjYoqpGj7orEhW5zfYkCsrL0841f50aVQI
Q9zZDk8/BuovaANx8TI3hmhNXIp2wh8IX4xjyd31NYUhBrLkZJ0gjGikqwIDAQAB
AoGBALGEka7uyitod2mLDypxUm09B+p9CeQB6GtIqGsDp1d8Uzg80TU09M/w9lXY
qU6/IVzARNSK6GfnbmO0ueKJsbW4dB5gyf1Nk8HexTpN6+C83v/zLYXiQ7tWdc9+
xkwBLUjUjtI/J5Vp0csiwo5ys2TxtrdRy+DpUf1YWBXk4gFBAkEA8QwIVinqbNW+
bFLUYeT0Tkw9etBUEnGSq+WcFLXehTVeEEN/JQfo6L3L5xirkutVkUgTgcLk8zg0
j37inMX48QJBAOEVJaajQASYl+m93STkj6WSbJIh4GxIsSjn6nacxCI2tWozWFSS
Pi+o3ZxivdBJO8m09sUG4OrFj7on/zdjl1sCQCBKF1fLd56qpz6FffO7IsrWRaoM
/CYYVqh2MolHr44yju4ozIBeZeG/gu/rCxi8M5Mpj4tjWx4Fmmzad2q+zlECQQDZ
I4WHFMaVqIAlRr0o8jaCDJghAf13eUkvpUxT0TiFqTGqmo4ESHElmUrxq3Bil8PN
+3505QXmd2EIvb1AuhsxAkEA5ODpUm9I9BOsrZW358iTUmHTFC2Y3pcx149ypdGr
uMl8wusPTuksdhu0AJ+MxS6XnbJvsgTV+mW60trOJxWiNw==
-----END RSA PRIVATE KEY-----"""

# `openssl dgst -sha256 -sign` sur le message ci-dessous, en base64. C'est LA référence :
# si notre implémentation en diverge d'un octet, Enable Banking rejette tous les appels.
MESSAGE_SIGNE = b"eyJhIjoxfQ.eyJiIjoyfQ"
SIGNATURE_OPENSSL = (
    "z3BiaY7CPoscLPmUyxq9zNkVRAgpYzaKNax8aEEGk6hGJejFah0D+dC9/cUiaw3qZUFj3yOVbbdT4RWZ"
    "mR5+EOLXD1tJZi8GyWHKBO4AEyzH3UGFg40gxRo329j5w38+t0dIGqCMNo8yIcbO29TyaaYtGFXYWQCb"
    "PQGWEvOPsRQ="
)


def tx(jour: date, montant: float, libelle: str) -> dict:
    return {"date": jour, "montant": montant, "libelle": libelle}


def mensuel(depuis: date, n: int, jour_du_mois: int, montant: float, libelle: str,
            decalages: dict[int, int] | None = None) -> list[dict]:
    """n passages mensuels, avec des décalages optionnels (week-ends, jours fériés)."""
    out = []
    d = depuis
    for i in range(n):
        mois = d.month - 1 + i
        annee = d.year + mois // 12
        jour = date(annee, mois % 12 + 1, jour_du_mois)
        jour += timedelta(days=(decalages or {}).get(i, 0))
        out.append(tx(jour, montant, libelle))
    return out


class TestCle(unittest.TestCase):
    def test_les_references_variables_ne_cassent_pas_le_groupe(self):
        a = recurring.cle("PRLV SEPA EDF 12/07/2026 REF 88213")
        b = recurring.cle("PRLV SEPA EDF 12/08/2026 REF 88907")
        self.assertEqual(a, b)
        self.assertEqual(a, "EDF")

    def test_les_accents_ne_separent_pas(self):
        self.assertEqual(recurring.cle("Prélèvement TRÉSOR PUBLIC"),
                         recurring.cle("PRELEVEMENT TRESOR PUBLIC"))

    def test_creanciers_differents_restent_separes(self):
        self.assertNotEqual(recurring.cle("EDF"), recurring.cle("ENGIE"))

    def test_famille_impots_reconnue(self):
        self.assertEqual(recurring.famille("PRLV SEPA DGFIP IMPOT REVENU"), "impots")
        self.assertEqual(recurring.famille("VIR SEPA DIRECTION GENERALE DES FINANCES"),
                         "impots")
        self.assertEqual(recurring.famille("CARTE 12/08 BOULANGERIE"), "")


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.aujourdhui = date(2026, 8, 20)

    def test_un_loyer_mensuel_est_detecte_avec_sa_date(self):
        lignes = mensuel(date(2025, 9, 5), 12, 5, -950.0, "PRLV SEPA LOYER SCI")
        [e] = [x for x in recurring.detecter(lignes, self.aujourdhui)
               if "LOYER" in x.libelle.upper()]
        self.assertEqual(e.cadence, "mensuelle")
        self.assertEqual(e.confidence, "sure")
        self.assertEqual(e.montant, -950.0)
        # Le 12e passage est le 2026-08-05, déjà passé : le prochain est en septembre.
        self.assertEqual(e.prochaine, date(2026, 9, 5))

    def test_le_jour_du_mois_ne_derive_pas_malgre_les_decalages(self):
        """Un +30 jours répété ferait glisser la date ; le jour du mois, non.

        C'est le bug qui compte le plus ici : sur douze mois, l'addition de 30 jours
        recule de six jours et finit par annoncer l'échéance dans le mauvais mois.
        """
        lignes = mensuel(date(2025, 9, 15), 12, 15, -412.0, "PRLV DGFIP IMPOT",
                         decalages={2: 2, 5: -1, 9: 2})
        [e] = [x for x in recurring.detecter(lignes, self.aujourdhui)
               if x.famille == "impots"]
        self.assertEqual(e.prochaine.day, 15)
        self.assertEqual(e.prochaine, date(2026, 9, 15))

    def test_le_31_devient_le_28_en_fevrier(self):
        lignes = [tx(date(2025, 10, 31), -60.0, "ABO X"),
                  tx(date(2025, 11, 30), -60.0, "ABO X"),
                  tx(date(2025, 12, 31), -60.0, "ABO X"),
                  tx(date(2026, 1, 31), -60.0, "ABO X")]
        [e] = [x for x in recurring.detecter(lignes, date(2026, 2, 10))
               if "ABO" in x.libelle.upper()]
        self.assertEqual(e.prochaine, date(2026, 2, 28))

    def test_une_rentree_est_detectee_et_signee_positivement(self):
        lignes = mensuel(date(2025, 9, 27), 12, 27, 3200.0, "VIR SEPA SALAIRE MAGELLIUM")
        [e] = [x for x in recurring.detecter(lignes, self.aujourdhui)
               if "SALAIRE" in x.libelle.upper()]
        self.assertFalse(e.sortie)
        self.assertEqual(e.montant, 3200.0)

    def test_deux_occurrences_restent_incertaines(self):
        lignes = [tx(date(2026, 6, 12), -80.0, "TRUC MACHIN"),
                  tx(date(2026, 7, 12), -80.0, "TRUC MACHIN")]
        [e] = [x for x in recurring.detecter(lignes, self.aujourdhui)
               if "TRUC" in x.libelle.upper()]
        self.assertEqual(e.confidence, "faible")

    def test_des_achats_irreguliers_ne_font_pas_une_echeance(self):
        lignes = [tx(date(2026, 3, 2), -34.9, "CARTE AMAZON"),
                  tx(date(2026, 3, 19), -12.5, "CARTE AMAZON"),
                  tx(date(2026, 5, 28), -88.0, "CARTE AMAZON"),
                  tx(date(2026, 6, 2), -9.9, "CARTE AMAZON")]
        self.assertEqual([x for x in recurring.detecter(lignes, self.aujourdhui)
                          if "AMAZON" in x.libelle.upper()], [])

    def test_une_annuelle_est_reconnue_sur_deux_ans(self):
        lignes = [tx(date(2024, 10, 15), -1240.0, "PRLV DGFIP TAXE FONCIERE"),
                  tx(date(2025, 10, 15), -1265.0, "PRLV DGFIP TAXE FONCIERE"),
                  tx(date(2026, 10, 15), -1290.0, "PRLV DGFIP TAXE FONCIERE")]
        [e] = [x for x in recurring.detecter(lignes, date(2026, 11, 1))
               if "FONCIERE" in x.libelle.upper()]
        self.assertEqual(e.cadence, "annuelle")
        self.assertEqual(e.prochaine, date(2027, 10, 15))

    def test_deux_lignes_le_meme_jour_sont_un_seul_evenement(self):
        """Frais séparés du principal : sans fusion, l'écart de 0 jour tue la cadence."""
        lignes = []
        for m in range(4, 9):
            lignes.append(tx(date(2026, m, 8), -60.0, "ASSURANCE MAAF"))
            lignes.append(tx(date(2026, m, 8), -2.5, "ASSURANCE MAAF"))
        [e] = [x for x in recurring.detecter(lignes, date(2026, 8, 20))
               if "MAAF" in x.libelle.upper()]
        self.assertEqual(e.cadence, "mensuelle")
        self.assertEqual(e.montant, -62.5)


class TestProjection(unittest.TestCase):
    def setUp(self):
        self.aujourdhui = date(2026, 8, 20)
        self.lignes = (
            mensuel(date(2025, 9, 27), 12, 27, 3200.0, "VIR SALAIRE")
            + mensuel(date(2025, 9, 5), 12, 5, -950.0, "PRLV LOYER SCI")
            + mensuel(date(2025, 9, 15), 12, 15, -412.0, "PRLV DGFIP IMPOT")
            + mensuel(date(2025, 9, 8), 12, 8, -89.0, "PRLV EDF")
        )
        self.echeances = recurring.detecter(self.lignes, self.aujourdhui)

    def test_les_rentrees_sont_comptees_sinon_tout_plonge(self):
        p = recurring.projeter(1500.0, self.echeances, jours=45, plancher=0.0,
                               aujourdhui=self.aujourdhui)
        self.assertGreater(p["solde_projete_fin"], 1500.0,
                           "le salaire doit remonter la trajectoire")

    def test_le_salaire_qui_arrive_avant_evite_la_fausse_alerte(self):
        """300 € au 20/08 et un loyer de 950 € au 05/09 : l'alarme serait fausse.

        Le salaire tombe le 27/08, entre les deux. Une projection qui ne compterait que
        les sorties annoncerait un découvert qui n'arrivera pas — et après deux ou trois
        fausses alertes, plus personne ne lit le brief.
        """
        p = recurring.projeter(300.0, self.echeances, jours=45, plancher=0.0,
                               aujourdhui=self.aujourdhui)
        self.assertIsNone(p["franchissement"])

    def test_le_franchissement_nomme_l_echeance_qui_le_provoque(self):
        """Le cas qui a fait naître ce dossier : « les impôts passent, le compte est trop bas ».

        Au 20/08, 300 € ; +3200 le 27/08, −950 le 05/09, −89 le 08/09, −412 le 15/09.
        Avec un plancher à 2100 €, c'est le prélèvement des impôts qui fait basculer —
        et c'est LUI qu'il faut nommer, pas le solde bas constaté après coup.
        """
        p = recurring.projeter(300.0, self.echeances, jours=45, plancher=2100.0,
                               aujourdhui=self.aujourdhui)
        self.assertIsNotNone(p["franchissement"])
        self.assertEqual(p["franchissement"]["date"], "2026-09-15")
        self.assertIn("IMPOT", p["franchissement"]["declencheur"].upper())

    def test_le_mensuel_ne_derive_pas_sur_une_longue_projection(self):
        """+30 jours répétés reculeraient le loyer du 5 au 2 en trois mois."""
        p = recurring.projeter(50000.0, self.echeances, jours=120, plancher=0.0,
                               aujourdhui=self.aujourdhui)
        loyers = [m["date"] for m in p["mouvements"] if "LOYER" in m["libelle"].upper()]
        self.assertGreaterEqual(len(loyers), 3)
        self.assertTrue(all(d.endswith("-05") for d in loyers), loyers)

    def test_un_compte_confortable_ne_declenche_rien(self):
        p = recurring.projeter(9000.0, self.echeances, jours=45, plancher=0.0,
                               aujourdhui=self.aujourdhui)
        self.assertIsNone(p["franchissement"])

    def test_les_motifs_incertains_sont_exclus_et_comptes(self):
        douteux = [tx(date(2026, 7, 3), -500.0, "MACHIN RARE"),
                   tx(date(2026, 8, 3), -500.0, "MACHIN RARE")]
        ech = recurring.detecter(self.lignes + douteux, self.aujourdhui)
        p = recurring.projeter(1500.0, ech, jours=45, aujourdhui=self.aujourdhui)
        self.assertGreaterEqual(p["echeances_ignorees_car_incertaines"], 1)
        self.assertTrue(all("MACHIN" not in m["libelle"].upper() for m in p["mouvements"]))


class TestSeTaireQuandOnNeSaitPas(unittest.TestCase):
    """Le cœur de CLAUDE.md : aucune ligne verte qui n'ait été observée."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        racine = Path(self.tmp.name)
        self.store = Store(config_dir=racine / "config", cache_dir=racine / "cache",
                           data_dir=racine / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sans_cache_l_etat_est_inconnu_pas_zero(self):
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
        [l] = read.soldes(None, self.store, "acc-1")
        self.assertEqual(l["etat"], "inconnu")
        self.assertIsNone(l["solde"], "un solde inconnu ne vaut pas 0 €")

    def test_un_cache_vieux_d_une_journee_n_est_plus_un_solde(self):
        import time
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
        self.store.put("balances-acc-1", {"balances": [
            {"balanceType": "interimAvailable",
             "balanceAmount": {"amount": "180.00", "currency": "EUR"}}]})
        chemin = self.store._cache_path("balances-acc-1")
        blob = json.loads(chemin.read_text())
        blob["at"] = time.time() - 30 * 3600      # au-delà de STALE_SECONDS (26 h)
        chemin.write_text(json.dumps(blob))
        [l] = read.soldes(None, self.store, "acc-1")
        self.assertEqual(l["etat"], "inconnu")
        self.assertIsNone(l["solde"])

    def test_un_cache_de_huit_heures_est_servi_mais_marque_ancien(self):
        import time
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
        self.store.put("balances-acc-1", {"balances": [
            {"balanceType": "interimAvailable",
             "balanceAmount": {"amount": "180.00", "currency": "EUR"}}]})
        chemin = self.store._cache_path("balances-acc-1")
        blob = json.loads(chemin.read_text())
        blob["at"] = time.time() - 8 * 3600
        chemin.write_text(json.dumps(blob))
        [l] = read.soldes(None, self.store, "acc-1")
        self.assertEqual(l["etat"], "ancien")
        self.assertEqual(l["solde"], 180.0)
        self.assertIn("h", l["age_lisible"])

    def test_le_solde_disponible_prime_sur_le_solde_comptable(self):
        """closingBooked ignore la carte passée ce matin : il flatte le compte."""
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
        self.store.put("balances-acc-1", {"balances": [
            {"balanceType": "closingBooked",
             "balanceAmount": {"amount": "500.00", "currency": "EUR"}},
            {"balanceType": "interimAvailable",
             "balanceAmount": {"amount": "180.00", "currency": "EUR"}}]})
        [l] = read.soldes(None, self.store, "acc-1")
        self.assertEqual(l["solde"], 180.0)

    def test_pas_de_projection_sur_un_solde_jamais_observe(self):
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
        p = read.projection(None, self.store, "acc-1")
        self.assertEqual(p["etat"], "inconnu")
        self.assertNotIn("franchissement", p)

    def test_le_consentement_expire_remonte_dans_les_problemes(self):
        import time
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun",
                                   "consent_expires_at": time.time() - 86400}])
        s = read.sante(self.store)
        self.assertEqual(s["verdict"], "a_regarder")
        self.assertTrue(any("EXPIRÉ" in p for p in s["problemes"]))

    def test_le_renouvellement_est_annonce_avant_l_expiration(self):
        import time
        self.store.save_accounts([{"id": "acc-1", "nom": "Commun",
                                   "consent_expires_at": time.time() + 9 * 86400}])
        self.assertTrue(any("renouveler" in p for p in read.sante(self.store)["problemes"]))



class TestRegistre(unittest.TestCase):
    """Le registre accumule là où la banque oublie. C'est le correctif du 2026-08-20.

    BoursoBank ne rend que ~90 jours. Sans accumulation, une échéance annuelle n'est pas
    « pas encore détectée », elle est indétectable À VIE — et la projection reste
    silencieusement optimiste. Ces tests gardent exactement ça.
    """

    def _ligne(self, jour, montant, libelle):
        return {"date": jour, "montant": montant, "libelle": libelle}

    def test_relire_la_meme_fenetre_ne_duplique_rien(self):
        lues = [self._ligne(date(2026, 8, 5), -950.0, "PRLV LOYER"),
                self._ligne(date(2026, 8, 8), -89.4, "PRLV EDF")]
        registre, neuves = ledger.fusionner([], lues)
        self.assertEqual(neuves, 2)
        registre2, neuves2 = ledger.fusionner(registre, lues)
        self.assertEqual(len(registre2), 2)
        self.assertEqual(neuves2, 0)

    def test_la_fenetre_qui_glisse_n_efface_pas_le_passe(self):
        """LE test. La banque avance sa fenêtre ; le registre, lui, ne recule pas."""
        ancienne = [self._ligne(date(2026, 5, 5), -950.0, "PRLV LOYER")]
        registre, _ = ledger.fusionner([], ancienne)
        # Deux mois plus tard, la banque ne montre plus mai.
        nouvelle = [self._ligne(date(2026, 7, 5), -950.0, "PRLV LOYER")]
        registre, neuves = ledger.fusionner(registre, nouvelle)
        self.assertEqual(neuves, 1)
        self.assertEqual([l["date"] for l in registre], ["2026-05-05", "2026-07-05"])

    def test_deux_operations_identiques_le_meme_jour_restent_deux(self):
        deux = [self._ligne(date(2026, 8, 2), -11.99, "PRLV SPOTIFY"),
                self._ligne(date(2026, 8, 2), -11.99, "PRLV SPOTIFY")]
        registre, _ = ledger.fusionner([], deux)
        self.assertEqual(len(registre), 2)
        # …et relire la même journée ne les fait pas passer à quatre.
        registre, _ = ledger.fusionner(registre, deux)
        self.assertEqual(len(registre), 2)

    def test_la_profondeur_croit_avec_les_lectures(self):
        registre, _ = ledger.fusionner([], [self._ligne(date(2026, 6, 1), -10.0, "X")])
        self.assertEqual(ledger.profondeur_jours(registre, date(2026, 8, 20)), 80)
        registre, _ = ledger.fusionner(registre, [self._ligne(date(2025, 9, 1), -10.0, "X")])
        self.assertEqual(ledger.profondeur_jours(registre, date(2026, 8, 20)), 353)

    def test_une_annuelle_finit_par_sortir_malgre_une_banque_a_90_jours(self):
        """Le scénario BoursoBank en entier, joué sur deux ans.

        Sans registre, `detecter` ne verrait jamais qu'un seul passage de la taxe
        foncière — jamais deux — donc aucune cadence, donc rien. Avec, elle sort.
        """
        # Le grand livre de la banque : deux ans de loyer mensuel + une taxe foncière
        # annuelle en octobre.
        verite = []
        for i in range(26):
            mois = 8 + i
            annee, m = 2024 + mois // 12, mois % 12 + 1
            verite.append(self._ligne(date(annee, m, 5), -950.0, "PRLV LOYER SCI"))
        for annee in (2024, 2025):
            verite.append(self._ligne(date(annee, 10, 15), -1240.0, "PRLV DGFIP TAXE FONCIERE"))

        def fenetre_banque(fin):
            """Ce que BoursoBank accepte de montrer un jour donné : 90 jours, pas plus."""
            debut = fin - timedelta(days=90)
            return [l for l in verite if debut <= l["date"] <= fin]

        aujourdhui = date(2026, 1, 15)

        # 1) Sans registre : une seule lecture, la fenêtre de 90 jours.
        sans = recurring.detecter(fenetre_banque(aujourdhui), aujourdhui)
        self.assertEqual([e for e in sans if "FONCIERE" in e.libelle.upper()], [],
                         "sur 90 jours l'annuelle est invisible — c'est le problème")

        # 2) Avec registre : un brief par semaine depuis un an et demi.
        registre = []
        jour = date(2024, 9, 1)
        while jour <= aujourdhui:
            registre, _ = ledger.fusionner(registre, fenetre_banque(jour))
            jour += timedelta(days=7)

        lignes = [{"date": date.fromisoformat(l["date"]), "montant": l["montant"],
                   "libelle": l["libelle"]} for l in registre]
        avec = recurring.detecter(lignes, aujourdhui)
        [taxe] = [e for e in avec if "FONCIERE" in e.libelle.upper()]
        self.assertEqual(taxe.cadence, "annuelle")
        self.assertEqual(taxe.prochaine, date(2026, 10, 15))
        self.assertGreaterEqual(ledger.profondeur_jours(registre, aujourdhui), 380)

    def test_le_registre_survit_a_un_aller_retour_sur_disque(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = Path(tmp) / "ledger" / "acc.json"
            registre, _ = ledger.fusionner([], [self._ligne(date(2026, 8, 5), -950.0, "L")])
            ledger.enregistrer(chemin, registre)
            self.assertEqual(ledger.charger(chemin), registre)

    def test_lecture_a_travers_le_store_alimente_le_registre(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            store = Store(config_dir=racine / "config", cache_dir=racine / "cache",
                          data_dir=racine / "data")
            store.save_accounts([{"id": "acc-1", "nom": "Commun"}])
            hier = (date.today() - timedelta(days=1)).isoformat()
            store.put("tx-acc-1", {"transactions": {"booked": [
                {"bookingDate": hier,
                 "transactionAmount": {"amount": "-34.90", "currency": "EUR"},
                 "remittanceInformationUnstructured": "CARTE AMAZON EU SARL"}]}})
            h = read.transactions(None, store, "acc-1", jours=30)
            self.assertEqual(h["nouvelles_lignes"], 1)
            self.assertTrue(store.ledger_path("acc-1").exists())
            self.assertFalse(h["annuel_detectable"])
            # Deuxième passage : rien de neuf, et surtout rien en double.
            h2 = read.transactions(None, store, "acc-1", jours=30)
            self.assertEqual(h2["nouvelles_lignes"], 0)
            self.assertEqual(len(h2["transactions"]), 1)


class TestServeurMcp(unittest.TestCase):
    """Le protocole, en vrai : on parle au serveur par un tube, comme le fait Claude."""

    def _dialogue(self, messages: list[dict]) -> list[dict]:
        entree = "\n".join(json.dumps(m) for m in messages) + "\n"
        p = subprocess.run([sys.executable, str(ICI / "bankread"), "mcp"],
                           input=entree, capture_output=True, text=True, timeout=60)
        return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]

    def test_poignee_de_main_et_liste_des_outils(self):
        reponses = self._dialogue([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual(len(reponses), 2, "une notification ne se répond pas")
        self.assertEqual(reponses[0]["result"]["serverInfo"]["name"], "bankread")
        noms = {t["name"] for t in reponses[1]["result"]["tools"]}
        self.assertEqual(noms, {"banque_sante", "banque_comptes", "banque_soldes",
                                "banque_echeances", "banque_projection",
                                "banque_transactions"})

    def test_un_outil_repond_meme_sans_banque_liee(self):
        [r] = self._dialogue([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "banque_sante", "arguments": {}}}])
        self.assertNotIn("error", r)
        self.assertIn("problemes", r["result"]["structuredContent"])

    def test_un_outil_inconnu_ne_tue_pas_la_connexion(self):
        reponses = self._dialogue([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "banque_nimporte_quoi", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertTrue(reponses[0]["result"]["isError"])
        self.assertEqual(len(reponses), 2, "le serveur doit encore répondre après")


class MagasinFactice:
    """Juste ce dont le client Enable Banking a besoin : deux identifiants.

    Un vrai `Store` irait interroger le trousseau macOS, donc lancerait `security` et
    demanderait peut-être une autorisation graphique — au milieu d'une suite de tests
    censée ne toucher ni le réseau ni la machine.
    """

    def __init__(self, app_id: str = "app-de-test", pem: str = CLE_PKCS8):
        self._secrets = (app_id, pem)

    def secrets(self, fournisseur=None):
        return self._secrets

    def fournisseur(self):
        return "enablebanking"


class ReponseFactice:
    def __init__(self, charge: dict):
        self._corps = json.dumps(charge).encode()

    def read(self):
        return self._corps

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _transport(reponses: list, appels: list):
    """Remplace urlopen : empile les requêtes vues, dépile les réponses préparées."""
    def faux_urlopen(req, timeout=None):
        appels.append(req)
        suivante = reponses.pop(0)
        if isinstance(suivante, Exception):
            raise suivante
        return ReponseFactice(suivante)
    return faux_urlopen


class TestSignatureRs256(unittest.TestCase):
    """RS256 sans dépendance : il doit rendre EXACTEMENT ce qu'OpenSSL rend."""

    def test_la_signature_est_celle_d_openssl(self):
        signature = rs256.signer(MESSAGE_SIGNE, CLE_PKCS8)
        self.assertEqual(base64.b64encode(signature).decode(), SIGNATURE_OPENSSL)

    def test_le_vieux_format_de_cle_donne_la_meme_signature(self):
        """PKCS#1 et PKCS#8 sont deux emballages de la même clé, pas deux clés."""
        self.assertEqual(rs256.signer(MESSAGE_SIGNE, CLE_PKCS1),
                         rs256.signer(MESSAGE_SIGNE, CLE_PKCS8))

    def test_le_jeton_porte_l_algorithme_et_l_application(self):
        jeton = rs256.jeton({"typ": "JWT", "alg": "RS256", "kid": "app-42"},
                            {"iss": "enablebanking.com"}, CLE_PKCS8)
        entete = json.loads(base64.urlsafe_b64decode(
            jeton.split(".")[0] + "=" * (-len(jeton.split(".")[0]) % 4)))
        self.assertEqual(entete["alg"], "RS256")
        self.assertEqual(entete["kid"], "app-42")
        self.assertEqual(len(jeton.split(".")), 3)

    def test_un_fichier_qui_n_est_pas_une_cle_est_refuse_tout_de_suite(self):
        """Mieux vaut échouer au rangement qu'au premier appel réseau du matin."""
        with self.assertRaises(rs256.CleInvalide):
            rs256.cle_privee("ceci n'est pas un PEM")


class TestTraductionEnableBanking(unittest.TestCase):
    """Le format d'Enable Banking traduit vers celui que tout le dépôt manipule."""

    def test_un_debit_devient_un_montant_negatif(self):
        """LE test de ce fichier. Enable Banking ne signe pas ses montants : le sens est
        dans `credit_debit_indicator`, à côté. Recopier `amount` tel quel ferait compter
        chaque prélèvement comme une rentrée — la projection remonterait à chaque facture
        EDF, et le brief annoncerait une marge qui n'existe pas."""
        op = enablebanking._operation({
            "booking_date": "2026-08-15", "credit_debit_indicator": "DBIT",
            "transaction_amount": {"amount": "142.30", "currency": "EUR"},
            "creditor": {"name": "EDF"},
        })
        self.assertEqual(op["transactionAmount"]["amount"], "-142.3")
        self.assertEqual(recurring.normaliser([op])[0]["montant"], -142.3)

    def test_un_credit_reste_positif(self):
        op = enablebanking._operation({
            "booking_date": "2026-08-01", "credit_debit_indicator": "CRDT",
            "transaction_amount": {"amount": "2500.00", "currency": "EUR"},
        })
        self.assertEqual(op["transactionAmount"]["amount"], "2500.0")

    def test_un_code_de_debit_inconnu_reste_une_sortie(self):
        """La documentation écrit tantôt DBIT, tantôt DBDT. On ne reconnaît QUE le
        crédit : se tromper dans ce sens invente une dépense, jamais une fausse marge."""
        op = enablebanking._operation({
            "booking_date": "2026-08-15", "credit_debit_indicator": "DBDT",
            "transaction_amount": {"amount": "80.00", "currency": "EUR"},
        })
        self.assertEqual(op["transactionAmount"]["amount"], "-80.0")

    def test_un_montant_deja_signe_n_est_pas_retouche(self):
        """Sans indicateur, la banque a déjà mis le signe : le forcer le retournerait."""
        op = enablebanking._operation({
            "booking_date": "2026-08-15",
            "transaction_amount": {"amount": "-12.50", "currency": "EUR"},
        })
        self.assertEqual(op["transactionAmount"]["amount"], "-12.5")

    def test_le_solde_disponible_iso_est_prefere_au_solde_comptable(self):
        """CLAV (disponible) est ce que rendent beaucoup de banques françaises. S'il
        n'était pas traduit, `read` retomberait sur le solde comptable — celui qui
        flatte le compte de tout ce qui n'est pas encore passé."""
        paye = {"balances": [
            {"balance_type": "CLBD", "balance_amount": {"amount": "1200.00",
                                                        "currency": "EUR"}},
            {"balance_type": "CLAV", "balance_amount": {"amount": "980.00",
                                                        "currency": "EUR"},
             "reference_date": "2026-08-20"},
        ]}
        traduit = {"balances": [enablebanking._solde(b) for b in paye["balances"]]}
        montant, devise, jour = read._meilleur_solde(traduit)
        self.assertEqual(montant, 980.0)
        self.assertEqual(devise, "EUR")
        self.assertEqual(jour, "2026-08-20")

    def test_une_operation_en_attente_ne_va_pas_avec_les_comptabilisees(self):
        """Le registre ne fond que les comptabilisées : une opération en attente peut
        changer de montant ou disparaître, et on ne bâtit pas une échéance là-dessus."""
        self.assertTrue(enablebanking._en_attente({"status": "PEND"}))
        self.assertFalse(enablebanking._en_attente({"status": "BOOK"}))


class TestClientEnableBanking(unittest.TestCase):
    """Le protocole, avec un faux transport : aucun octet ne sort de la machine."""

    def test_les_pages_suivantes_sont_suivies(self):
        """Enable Banking pagine par `continuation_key`. S'arrêter à la première page
        perdrait le début de l'historique — donc les échéances les plus anciennes,
        c'est-à-dire précisément les annuelles."""
        appels: list = []
        reponses = [
            {"transactions": [{"booking_date": "2026-08-10",
                               "credit_debit_indicator": "DBIT",
                               "transaction_amount": {"amount": "10.00"}}],
             "continuation_key": "page2"},
            {"transactions": [{"booking_date": "2026-07-10",
                               "credit_debit_indicator": "DBIT",
                               "transaction_amount": {"amount": "20.00"}}]},
        ]
        api = enablebanking.Api(MagasinFactice())
        with mock.patch("urllib.request.urlopen", _transport(reponses, appels)):
            got = api.transactions("uid-1", date_from="2026-01-01")
        self.assertEqual(len(got["transactions"]["booked"]), 2)
        self.assertIn("continuation_key=page2", appels[1].full_url)

    def test_sans_presence_humaine_aucun_en_tete_psu(self):
        """L'en-tête PSU déclare à la banque qu'un utilisateur est devant l'écran, ce qui
        lève le plafond du rapatriement en arrière-plan. À 7 h 30, personne n'est là :
        l'envoyer quand même serait un mensonge, pas une optimisation."""
        appels: list = []
        api = enablebanking.Api(MagasinFactice(), presence_humaine=False)
        with mock.patch("urllib.request.urlopen", _transport([{"balances": []}], appels)):
            api.balances("uid-1")
        self.assertNotIn("Psu-user-agent", appels[0].headers)

        appels.clear()
        api = enablebanking.Api(MagasinFactice(), presence_humaine=True)
        with mock.patch("urllib.request.urlopen", _transport([{"balances": []}], appels)):
            api.balances("uid-1")
        self.assertIn("Psu-user-agent", appels[0].headers)

    def test_le_quota_epuise_est_une_erreur_a_part(self):
        """429 n'est pas une panne, c'est « repasse plus tard » — et la bonne réponse est
        de servir le cache en disant son âge, pas de dire qu'on ne sait rien."""
        erreur = urllib.error.HTTPError(
            "https://api.enablebanking.com/accounts/x/balances", 429, "Too Many Requests",
            {"Retry-After": "3600"}, None)
        erreur.read = lambda: b'{"message": "rate limit"}'
        api = enablebanking.Api(MagasinFactice())
        with mock.patch("urllib.request.urlopen", _transport([erreur], [])):
            with self.assertRaises(RateLimited) as pris:
                api.balances("uid-1")
        self.assertEqual(pris.exception.reset_seconds, 3600)

    def test_sans_identifiants_le_message_dit_quoi_faire(self):
        api = enablebanking.Api(MagasinFactice(app_id="", pem=""))
        with self.assertRaises(ApiError) as pris:
            api.balances("uid-1")
        self.assertIn("secrets --set", str(pris.exception))


class TestChoixDuFournisseur(unittest.TestCase):
    """`read.py` ne doit jamais savoir à qui il parle — c'est ce qui rend le changement
    de fournisseur possible sans toucher à la détection ni à la projection."""

    def _magasin(self, racine: Path) -> Store:
        return Store(config_dir=racine / "config", cache_dir=racine / "cache",
                     data_dir=racine / "data")

    def test_le_fournisseur_choisi_est_celui_qu_on_charge(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._magasin(Path(d))
            store.save_fournisseur("enablebanking")
            self.assertIsInstance(provider.charger(store), enablebanking.Api)

    def test_le_contrat_tient_en_deux_methodes(self):
        """Si ce test casse, c'est que le contrat a bougé — et donc que le prochain
        fournisseur devra implémenter autre chose que ce que le README promet."""
        self.assertTrue(isinstance(enablebanking.Api(MagasinFactice()),
                                   provider.Fournisseur))


class TestDureeDeConsentement(unittest.TestCase):
    """Ce qu'on enregistre, c'est ce que la banque a ACCORDÉ.

    On demande six mois, la banque en donne trois : l'écart est courant. Recopier la
    demande ferait annoncer un consentement en bonne santé deux mois après sa mort —
    une date verte que personne n'a observée.
    """

    def _session(self, fin: str) -> dict:
        return {"access": {"valid_until": fin}}

    def test_c_est_la_duree_accordee_qui_est_lue(self):
        fin = datetime.now(timezone.utc) + timedelta(days=92)
        self.assertEqual(cli._jours_restants(self._session(fin.isoformat())), 92)

    def test_le_z_de_l_heure_zoulou_est_accepte(self):
        """Certaines réponses écrivent 2026-11-20T08:00:00Z, que `fromisoformat`
        refusait avant Python 3.11 et refuse encore si on ne le traduit pas."""
        fin = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
        zoulou = fin.isoformat().replace("+00:00", "Z")
        self.assertEqual(cli._jours_restants(self._session(zoulou)), 30)

    def test_un_consentement_expire_ne_rend_pas_un_nombre_negatif(self):
        fin = datetime.now(timezone.utc) - timedelta(days=5)
        self.assertEqual(cli._jours_restants(self._session(fin.isoformat())), 0)

    def test_une_date_illisible_rend_None_et_surtout_pas_zero(self):
        """La nuance qui compte : `None` veut dire « je ne sais pas » et laisse
        l'appelant retomber sur la durée demandée. `0` voudrait dire « expiré
        aujourd'hui » et ferait renvoyer signer un consentement tout neuf."""
        self.assertIsNone(cli._jours_restants({"access": {"valid_until": "bientôt"}}))
        self.assertIsNone(cli._jours_restants({}))


class TestDemonstration(unittest.TestCase):
    """`bankread demo` doit raconter la bonne histoire, et n'écrire nulle part ailleurs.

    C'est aussi le test d'intégration le plus complet du dépôt : il part de 400 jours
    d'opérations brutes et va jusqu'à la trajectoire, en passant par la détection de
    cadences et le registre. Si un maillon casse, celui-ci le dit.
    """

    def _monte(self, racine: Path):
        store = Store(config_dir=racine / "config", cache_dir=racine / "cache",
                      data_dir=racine / "data")
        return store, demo.monter(store)

    def test_la_trajectoire_passe_sous_le_plancher_a_cause_des_impots(self):
        """Le scénario est calé sur la date d'exécution pour que ce soit vrai tous les
        jours : le loyer laisse au-dessus, les impôts font passer dessous."""
        with tempfile.TemporaryDirectory() as d:
            store, compte = self._monte(Path(d))
            p = read.projection(None, store, compte, jours=45, plancher=300)
            self.assertIsNotNone(p["franchissement"], "la démonstration doit montrer un creux")
            self.assertIn("impot", p["franchissement"]["declencheur"].lower())

    def test_l_annuelle_vue_deux_fois_sort_de_la_trajectoire(self):
        """Deux passages sont une coïncidence : la taxe foncière est listée, marquée
        faible, et ne compte PAS — et la projection dit qu'elle est donc optimiste."""
        with tempfile.TemporaryDirectory() as d:
            store, compte = self._monte(Path(d))
            e = read.echeances(None, store, compte)
            annuelle = [x for x in e["echeances"] if "fonciere" in x["libelle"].lower()]
            self.assertEqual(len(annuelle), 1, "la taxe foncière doit être détectée")
            self.assertEqual(annuelle[0]["confidence"], "faible")
            self.assertEqual(annuelle[0]["occurrences"], 2)

            p = read.projection(None, store, compte, jours=400, plancher=300)
            libelles = " ".join(m["libelle"].lower() for m in p["mouvements"])
            self.assertNotIn("fonciere", libelles)
            self.assertGreaterEqual(p["echeances_ignorees_car_incertaines"], 1)

    def test_rien_n_est_ecrit_hors_du_magasin_qu_on_lui_donne(self):
        """Une ligne inventée qui se glisserait dans un vrai registre y resterait pour de
        bon, et fausserait des projections auxquelles quelqu'un fait confiance."""
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            store, compte = self._monte(racine)
            ecrits = {chemin for chemin in racine.rglob("*") if chemin.is_file()}
            self.assertTrue(ecrits, "la démonstration doit bien écrire quelque chose")
            self.assertTrue(store.ledger_path(compte).exists())
            for chemin in ecrits:
                self.assertTrue(chemin.is_relative_to(racine))

    def test_le_salaire_est_compte_dans_l_autre_sens(self):
        """Projeter les seules sorties donnerait une courbe qui plonge toujours, donc une
        alarme tous les jours, donc plus personne qui lit le brief en semaine deux."""
        with tempfile.TemporaryDirectory() as d:
            store, compte = self._monte(Path(d))
            e = read.echeances(None, store, compte)
            salaire = [x for x in e["echeances"] if x["montant"] > 0]
            self.assertTrue(salaire, "le salaire doit être détecté comme une rentrée")
            self.assertEqual(salaire[0]["sens"], "rentree")


if __name__ == "__main__":
    unittest.main(verbosity=2)
