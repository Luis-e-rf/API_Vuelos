"""Tests unitarios de los normalizadores deterministas (FASE 1).

Objetivo de cobertura: >90% de app/normalizers/*. Funciones puras:
sin red, sin LLM, sin estado. `date` recibe `hoy` inyectado para ser
reproducible.
"""
from __future__ import annotations

import datetime

import pytest

from app.normalizers import city, date, money, passengers


# --- CityNormalizer -------------------------------------------------------


class TestCity:
    def test_alias_multiword(self):
        assert city.normalizar("pa san andres") == "San Andres"

    def test_alias_con_tildes(self):
        assert city.normalizar("bogotá") == "Bogota"
        assert city.normalizar("quiero ir pa santa marta") == "Santa Marta"

    def test_alias_typo_conocido(self):
        assert city.normalizar("barajilla") == "Barranquilla"

    def test_difusso_ultimo_recurso(self):
        assert city.normalizar("cartajena") == "Cartagena"  # typo puro

    def test_iata_como_token_exacto(self):
        assert city.normalizar("CTG") == "Cartagena"
        assert city.normalizar("vuelo a MDE") == "Medellin"

    def test_sin_substring_falso_positivo(self):
        # regresión del bug legacy: 'pan' no debe matchear 'Panama'
        assert city.normalizar("me gusta el pan") is None
        assert city.normalizar("pan") is None

    def test_vacio_y_ruido(self):
        assert city.normalizar("") is None
        assert city.normalizar("hola mundo") is None
        assert city.normalizar("1234") is None

    def test_origen_y_destino_en_orden(self):
        hallados = city.extraer_ciudades("de Bogotá a San Andrés")
        assert [c for _, c in hallados] == ["Bogota", "San Andres"]
        assert hallados[0][0] < hallados[1][0]  # offsets ordenados

    def test_extraer_ciudades_vacio(self):
        assert city.extraer_ciudades("no hay ciudades aquí") == []

    def test_multiword_larga(self):
        assert city.normalizar("pa san jose del guaviare") == "San Jose del Guaviare"


# --- MoneyParser ----------------------------------------------------------


class TestMoney:
    def test_un_palo(self):
        m = money.parse("un palo")
        assert m.valor_cop == 1_000_000
        assert m.moneda == "COP"
        assert not m.por_persona

    def test_millon_por_persona(self):
        m = money.parse("1 millón por persona")
        assert m.valor_cop == 1_000_000
        assert m.por_persona

    def test_por_cabeza(self):
        assert money.parse("600 mil por cabeza").por_persona

    def test_sufijos_mil(self):
        assert money.parse("600 mil").valor_cop == 600_000
        assert money.parse("500k").valor_cop == 500_000
        assert money.parse("80 lucas").valor_cop == 80_000

    def test_millones_palabra(self):
        assert money.parse("dos millones").valor_cop == 2_000_000
        assert money.parse("3 melones").valor_cop == 3_000_000
        assert money.parse("2M").valor_cop == 2_000_000

    def test_separador_de_miles(self):
        assert money.parse("1.500.000").valor_cop == 1_500_000
        assert money.parse("1,500,000").valor_cop == 1_500_000

    def test_decimal_antes_del_sufijo(self):
        assert money.parse("1,5 millones").valor_cop == 1_500_000

    def test_moneda_extranjera(self):
        assert money.parse("300 dólares").valor_cop == 1_200_000
        assert money.parse("300 dólares").moneda == "USD"
        assert money.parse("200 verdes").valor_cop == 800_000
        assert money.parse("100 euros").valor_cop == 440_000
        assert money.parse("100 euros").moneda == "EUR"

    def test_cifra_desnuda_grande(self):
        assert money.parse("presupuesto 700000").valor_cop == 700_000

    def test_cifra_desnuda_ambigua(self):
        # '300' en COP es ambiguo -> None; con moneda sí es válido
        assert money.parse("300").valor_cop is None
        assert money.parse("300 dólares").valor_cop == 1_200_000

    def test_sin_monto(self):
        assert money.parse("hola").valor_cop is None
        assert money.parse("").valor_cop is None
        assert money.parse("busca vuelos").valor_cop is None

    def test_sin_monto_pero_marca_por_persona(self):
        m = money.parse("por persona nada más")
        assert m.valor_cop is None
        assert m.por_persona


# --- DateParser -----------------------------------------------------------


class TestDate:
    HOY = datetime.date(2026, 8, 24)

    def test_iso_directo(self):
        assert date.parse("2027-01-05", hoy=self.HOY) == "2027-01-05"

    def test_dia_mes_anyo(self):
        assert date.parse("5 de enero de 2027", hoy=self.HOY) == "2027-01-05"
        assert date.parse("15 dic 2027", hoy=self.HOY) == "2027-12-15"

    def test_periodos(self):
        assert date.parse("principios de enero 2027", hoy=self.HOY) == "2027-01-05"
        assert date.parse("mediados de marzo de 2027", hoy=self.HOY) == "2027-03-15"
        assert date.parse("final de abril 2027", hoy=self.HOY) == "2027-04-25"
        assert date.parse("finales de mayo 2027", hoy=self.HOY) == "2027-05-25"

    def test_mes_sin_periodo_es_mitad(self):
        assert date.parse("enero 2027", hoy=self.HOY) == "2027-01-15"

    def test_anyo_suelto(self):
        assert date.parse("2027", hoy=self.HOY) == "2027-01-15"

    def test_rollo_al_futuro_sin_anyo(self):
        # 5 de mayo ya pasó en HOY -> rueda a 2027
        assert date.parse("5 de mayo", hoy=self.HOY) == "2027-05-05"
        # 5 de diciembre aún no llega -> este año
        assert date.parse("5 de diciembre", hoy=self.HOY) == "2026-12-05"

    def test_anyo_explícito_pasado_se_respeta(self):
        # validar futuro es trabajo de SlotManager, no del parser
        assert date.parse("enero 2020", hoy=self.HOY) == "2020-01-15"

    def test_dia_invalido(self):
        assert date.parse("45 de enero 2027", hoy=self.HOY) is None
        assert date.parse("2027-13-40", hoy=self.HOY) is None

    def test_sin_fecha(self):
        assert date.parse("busca vuelos", hoy=self.HOY) is None
        assert date.parse("", hoy=self.HOY) is None


# --- Pasajeros ------------------------------------------------------------


class TestPasajeros:
    def test_numero_desnudo_tras_verbo(self):
        assert passengers.parse("somos 4") == 4
        assert passengers.parse("vamos 3") == 3
        assert passengers.parse("seremos dos") == 2

    def test_cantidad_mas_sustantivo(self):
        assert passengers.parse("2 personas") == 2
        assert passengers.parse("dos personas") == 2

    def test_adultos_y_nino(self):
        assert passengers.parse("van a ser 2 adultos y un niño") == 3

    def test_pareja(self):
        assert passengers.parse("somos pareja") == 2
        assert passengers.parse("matrimonio con presupuesto") == 2

    def test_familiar_y_yo(self):
        assert passengers.parse("somos mi esposa y yo") == 2
        assert passengers.parse("mi hija y yo") == 2
        assert passengers.parse("somos mi esposa, mi hijo y yo") == 3

    def test_limite_superior(self):
        assert passengers.parse("somos veinte") == 20

    def test_sin_evidencia(self):
        assert passengers.parse("hola") is None
        assert passengers.parse("") is None
        assert passengers.parse("busca a cartagena") is None

    def test_fuera_de_rango(self):
        assert passengers.parse("somos treinta") is None  # palabra no soportada
