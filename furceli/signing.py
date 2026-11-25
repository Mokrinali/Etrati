"""Digital signatures with certificates (PAdES), through pyHanko."""
from __future__ import annotations

import datetime
import io
import logging
import os
import secrets
import shutil

from . import files
from .engine import EngineError


logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)


def make_certificate(out, password, name):
    """A self-signed certificate: proves integrity, but nobody vouches for the name."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    who = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(who).issuer_name(who)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=5 * 365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=True, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    data = pkcs12.serialize_key_and_certificates(
        name.encode(), key, cert, None, BestAvailableEncryption(password.encode()))
    with open(out, "wb") as f:
        f.write(data)


def sign(src, out, password, pfx, pfx_password, o):
    """Sign a saved file into a new one; earlier signatures stay valid."""
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.writer import PdfReadError
    from pyhanko.sign import fields, signers
    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx, passphrase=pfx_password.encode())
    except Exception:
        signer = None
    if signer is None:
        raise EngineError("bad_certificate")
    with open(src, "rb") as f:
        data = io.BytesIO(f.read())
    try:
        w = IncrementalPdfFileWriter(data, strict=False)
        if w.prev.encrypted:
            w.encrypt(password.encode("utf-8"))
            if not hasattr(w._encrypt, "idnum"):  # MuPDF writes it inline, pyHanko wants a reference
                w._encrypt = w.add_object(generic.DictionaryObject(dict(w._encrypt)))
    except (PdfReadError, ValueError, KeyError):
        raise EngineError("damaged")
    name = "Signature" + secrets.token_hex(4)
    spec = fields.SigFieldSpec(name)
    style = looks = None
    if o.get("box"):
        spec = fields.SigFieldSpec(name, on_page=o["page"], box=tuple(int(x) for x in o["box"]))
        looks = files.new_session()
        try:
            style = _stamp(src, password, o, signer, looks)
        except Exception:
            shutil.rmtree(looks, ignore_errors=True)
            raise EngineError("sign_failed")
    meta = signers.PdfSignatureMetadata(
        field_name=name, reason=o.get("reason") or None, location=o.get("location") or None,
        subfilter=fields.SigSeedSubFilter.PADES)
    try:
        result = signers.PdfSigner(meta, signer, stamp_style=style, new_field_spec=spec).sign_pdf(
            w)
    except Exception as e:
        raise EngineError(f"sign_failed: {type(e).__name__}")
    finally:
        if looks:
            shutil.rmtree(looks, ignore_errors=True)
    with open(out, "wb") as f:
        f.write(result.getbuffer())
    check = verify(out, password)
    if not check or not check[-1]["intact"]:
        os.remove(out)
        raise EngineError("sign_failed")


def _stamp(src, password, o, signer, folder):
    """What a visible signature looks like, upright on rotated pages and in Georgian."""
    import html
    import pymupdf
    from pyhanko.pdf_utils.content import ImportedPdfPage
    from pyhanko.pdf_utils.layout import AxisAlignment, SimpleBoxLayoutRule
    from pyhanko.stamp import StaticStampStyle
    doc = pymupdf.open(src)
    if doc.needs_pass:
        doc.authenticate(password)
    turn = doc[o["page"]].rotation
    doc.close()
    x0, y0, x1, y1 = o["box"]
    w, h = abs(x1 - x0), abs(y1 - y0)
    name = signer.signing_cert.subject.native.get("common_name", "")
    rows = [f"<b>{html.escape(str(name))}</b>",
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")]
    if o.get("reason"):
        rows.append(html.escape(o["reason"]))
    look = pymupdf.open()
    page = look.new_page(width=w, height=h)
    page.draw_rect(page.rect + (.5, .5, -.5, -.5), color=(.2, .3, .6), width=.8)
    page.insert_htmlbox(page.rect + (4, 3, -4, -3), "<br>".join(rows),
                        css="*{font-family:sans-serif;font-size:11px;color:#1a2a5a}", rotate=turn)
    path = os.path.join(folder, "look.pdf")
    look.save(path)
    look.close()
    content = ImportedPdfPage(path)
    fill = SimpleBoxLayoutRule(x_align=AxisAlignment.ALIGN_MID, y_align=AxisAlignment.ALIGN_MID)
    return StaticStampStyle(background=content, background_layout=fill, background_opacity=1,
                            border_width=0)


def verify(path, password=""):
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    out = []
    with open(path, "rb") as f:
        try:
            r = PdfFileReader(f, strict=False)
            if r.encrypted:
                r.decrypt(password.encode("utf-8"))
            items = r.embedded_signatures
        except Exception:
            raise EngineError("damaged")
        for sig in items:
            row = {"field": str(sig.field_name), "signer": "", "time": "", "intact": False,
                   "trusted": False, "whole": False, "note": ""}
            try:
                cert = sig.signer_cert
                row["signer"] = str(cert.subject.human_friendly)
                row["self_signed"] = cert.self_signed != "no"
                st = validate_pdf_signature(
                    sig, ValidationContext(allow_fetching=False, trust_roots=_windows_roots()),
                    skip_diff=False)
                row["intact"] = bool(st.intact and st.valid)
                row["trusted"] = bool(st.trusted)
                row["whole"] = st.coverage.name in ("ENTIRE_FILE", "ENTIRE_REVISION")
                row["changed_later"] = st.coverage.name != "ENTIRE_FILE"
                row["modified_badly"] = not bool(getattr(st, "docmdp_ok", True))
                when = st.signer_reported_dt
                row["time"] = when.strftime("%Y-%m-%d %H:%M") if when else ""
            except Exception as e:
                row["note"] = f"{type(e).__name__}"
            out.append(row)
    return out


def _windows_roots():
    """Certificates this computer trusts."""
    import ssl
    from asn1crypto import x509
    roots = []
    # any purpose, e-mail protection, document signing
    fit = {"1.3.6.1.5.5.7.3.4", "1.3.6.1.4.1.311.10.3.12"}
    for data, encoding, trust in ssl.enum_certificates("ROOT"):
        if encoding == "x509_asn" and (trust is True or fit & set(trust or ())):
            try:
                roots.append(x509.Certificate.load(data))
            except ValueError:
                continue
    return roots
