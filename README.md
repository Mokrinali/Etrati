# ETRATI

<img src="furceli/assets/furceli.png" alt="ETRATI" width="96">

PDF-ის სანახავი და რედაქტირების პროგრამა Windows-ისთვის, ქართული ინტერფეისითა და ქართული ტექსტის მხარდაჭერით.

*A PDF viewer and editor for Windows with a Georgian interface and first-class support for Georgian text.*

## შესაძლებლობები

- **ნახვა:** ჩანართები, ძიება, სანიშნეები, მინიატურები, ბეჭდვა, პაროლიანი ფაილები
- **გვერდები:** გადალაგება, წაშლა, მობრუნება, დუბლირება, ჩამოჭრა, გაერთიანება, დაყოფა, ამოღება
- **კომენტარები და ფორმები:** მარკერი, შენიშვნა, ხატვა, შტამპი, ფორმის შევსება და შექმნა, ხელმოწერის სურათი
- **შიგთავსი:** სტრიქონის შეცვლა, ტექსტი, სურათი, წყლის ნიშანი, კოლონტიტული, Bates ნუმერაცია
- **OCR:** ქართული, ინგლისური, რუსული
- **სკანის გასუფთავება:** ფოტო, სქრინშოტი ან სურათოვანი PDF → გასწორებული, სუფთა და ძებნადი PDF (HEIC-იც)
- **კონვერტაცია:** Word, Excel, PowerPoint, HTML, სურათი და ტექსტი → PDF; PDF → Word, Excel, PowerPoint, HTML, ტექსტი, სურათი
- **უსაფრთხოება:** ტექსტის სამუდამო წაშლა (redaction), ფარული მონაცემების წაშლა, AES-256, სერტიფიკატით ხელმოწერა და შემოწმება
- **სხვა:** ორი ვერსიის შედარება, ფაილის შემცირება, სერიული დამუშავება, AI ასისტენტი (სურვილისამებრ, საკუთარი Anthropic API გასაღებით)
- **ძველი ქართული ფონტები** (AcadNusx, LitNusx და მსგავსი) ძიებისა და კოპირებისას Unicode-ად გადაიყვანება

## დაყენება

საჭიროა Windows 10/11 და Python 3.12.

```powershell
git clone https://github.com/Mokrinali/Etrati.git
cd Etrati
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -File scripts\get_tessdata.ps1    # OCR-ის ენები, დაახლოებით 35 მბ
.\.venv\Scripts\pythonw.exe etrati.pyw
powershell -File scripts\register_pdf.ps1    # სურვილისამებრ: PDF-ის გახსნა Etrati-ით
```

Office ფაილების გარდაქმნას დაყენებული Microsoft Office სჭირდება, HTML-ისას კი Microsoft Edge.

## ფაილების დაცვა

- ორიგინალი დასტურის გარეშე არ იწერება; გადაწერამდე იქმნება სარეზერვო ასლი (ბოლო 50).
- შენახული ფაილი ჯერ მოწმდება და მხოლოდ შემდეგ ცვლის ძველს.
- PDF ცალკე პროცესში იხსნება: დაზიანებული ფაილი ფანჯარას ვერ თიშავს.
- AI ასისტენტი ტექსტს მხოლოდ კითხვის დასმისას და თითო დოკუმენტზე ცალკე თანხმობით აგზავნის.

## ცნობილი შეზღუდვები

- ტექსტის შეცვლა სტრიქონობრივია, აბზაცი თავიდან არ გადაეწყობა.
- რთული Word/Excel ექსპორტი გადახედვას ითხოვს.
- სკანის გასუფთავება: ღია მაგიდაზე დადებული თეთრი ფურცლის კუთხეები ხშირად ხელით სწორდება; ბუნდოვან
  და წვრილ ტექსტზე ამოცნობა სუსტია. ციფრები გადაამოწმეთ.
- ფორმის JavaScript გამოთვლები არ სრულდება.
- PDF/A ექსპორტი ოფიციალური შემმოწმებლით არ მოწმდება; ხელმოწერისას სერტიფიკატის გაუქმება არ მოწმდება.

## ტესტები

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

## მესამე მხარის კომპონენტები

PyMuPDF / MuPDF (AGPL-3.0), Qt for Python (LGPL-3.0), Tesseract language data (Apache-2.0), pdf2docx (MIT),
python-docx (MIT), openpyxl (MIT), python-pptx (MIT), pyHanko (MIT), Pillow (MIT-CMU), pillow-heif (BSD; შეიცავს libheif-ს, LGPL-3.0),
OpenCV (Apache-2.0), fontTools (MIT),
lxml (BSD), Anthropic SDK (MIT). პროგრამის კოდის შიდა პაკეტს ისტორიულად `furceli` ჰქვია.
