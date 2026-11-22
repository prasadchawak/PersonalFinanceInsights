"""OFX / QFX parser (both the old SGML flavour and the XML flavour).

OFX is the one standardised export format most banks offer ("Quicken" / "Money" / "Web Connect").
Each transaction carries a bank-assigned FITID, which makes de-duplication exact.
"""
import re

from . import ParsedTxn
from ..normalize import fingerprint, parse_amount_cents

_TXN_BLOCK = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", re.S | re.I)
_FIELD = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)", re.I)


def is_ofx(text: str) -> bool:
    head = text[:2000].upper()
    return "OFXHEADER" in head or "<OFX>" in head or "<OFX " in head


def parse(text: str) -> list[ParsedTxn]:
    out: list[ParsedTxn] = []
    for block in _TXN_BLOCK.findall(text):
        fields = {k.upper(): v.strip() for k, v in _FIELD.findall(block)}
        if "DTPOSTED" not in fields or "TRNAMT" not in fields:
            continue
        d = fields["DTPOSTED"][:8]
        iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        amount = parse_amount_cents(fields["TRNAMT"])
        payee = fields.get("NAME") or fields.get("PAYEE") or fields.get("MEMO") or "(no description)"
        memo = fields.get("MEMO") if fields.get("NAME") else None
        fitid = fields.get("FITID")
        fp = fingerprint("ofx", fitid) if fitid else fingerprint("ofx", iso, amount, payee)
        out.append(ParsedTxn(iso, amount, payee, memo, fp, raw=fields))
    return out
