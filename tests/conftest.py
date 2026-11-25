import os
import sys

import pymupdf
import pytest

os.environ["FURCELI_TEST"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYLFAEN = "C:/Windows/Fonts/sylfaen.ttf"


def make_sample(path, pages=12):
    doc = pymupdf.open()
    for i in range(pages):
        landscape = i == 2
        p = doc.new_page(width=842 if landscape else 595, height=595 if landscape else 842)
        p.insert_font(fontname="sy", fontfile=SYLFAEN)
        p.insert_text((72, 90), f"თავი {i + 1}", fontname="sy", fontsize=26)
        for j in range(14):
            p.insert_text((72, 140 + j * 28),
                          f"გამარჯობა მსოფლიო, სტრიქონი {j + 1}. Hello world.",
                          fontname="sy", fontsize=13)
        p.draw_rect(pymupdf.Rect(60, 60, p.rect.width - 60, p.rect.height - 60),
                    color=(.2, .4, .8))
    doc[3].set_rotation(90)
    doc[4].insert_text((72, 700), "უნიკალური", fontname="sy", fontsize=14)
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 600, 200, 620),
                        "page": 5, "to": pymupdf.Point(0, 300)})
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 640, 200, 660),
                        "uri": "https://example.com/"})
    doc.set_toc([[1 if i % 3 == 0 else 2, f"თავი {i + 1}", i + 1] for i in range(pages)])
    doc.set_metadata({"title": "სატესტო დოკუმენტი", "author": "ავტორი"})
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture(scope="session")
def sample(tmp_path_factory):
    return make_sample(tmp_path_factory.mktemp("pdf") / "ნიმუში ერთი.pdf")


@pytest.fixture(scope="session")
def locked(tmp_path_factory, sample):
    out = str(tmp_path_factory.mktemp("pdf") / "locked.pdf")
    d = pymupdf.open(sample)
    d.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="საიდუმლო", owner_pw="owner",
           permissions=pymupdf.PDF_PERM_ACCESSIBILITY)
    d.close()
    return out
