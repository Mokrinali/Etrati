# icons-v2 — ETRATI-ის ახალი აიკონები

**სტატუსი: შეთავაზება.** მომხმარებელს ჯერ არ აურჩევია ერთ-ერთი:
- **A** — მთელი ნაკრები (46 აიკონი), Segoe Fluent Icons-ის ნაცვლად ყველგან.
- **B** — მხოლოდ „ახალი“ და „შეცვლილი“ (11 აიკონი), დანარჩენი `glyph()`-ით რჩება.

**ჯერ ჰკითხე, რომელი.** სანამ არ აირჩევს, კოდი არ შეცვალო.

## რა არის აქ

| ფაილი | რა არის |
|---|---|
| `preview.png` | ყველაფერი ერთ სურათზე: 10 პრობლემური ღილაკი („ახლა“ → „ახალი“), ტოლბარი ორ თემაში, სრული ნაკრები, მდგომარეობები. **ჯერ ეს ნახე.** |
| `preview.html` | იგივე ბრაუზერისთვის |
| `svg/*.svg` | თითო აიკონი: 24×24, `stroke="currentColor"`, ხაზი 1.75, მომრგვალებული ბოლოები, შევსების გარეშე |
| `mapping.json` | თითო აიკონზე: ფაილი, სად არის კოდში, ღილაკის სახელი, ახლანდელი აიკონი, სტატუსი (`new` / `changed` / `same`) |

„ახლა“ სვეტში Windows-ის აიკონები სურათზე მიახლოებით არის დახატული. ზუსტი ასლი ეს არ არის. ზუსტი კოდი `mapping.json`-ის `current` ველშია.

## ახალი — 6

აქამდე აიკონი არ ჰქონდათ, ტექსტი ან ტექსტის სიმბოლოები ეწერა.

| ფაილი | ღილაკი | კოდში | ახლა |
|---|---|---|---|
| `svg/fit-width.svg` | სიგანეზე მორგება | `window.py a_width` | — (აიკონი არ აქვს, ზოლში ტექსტია) |
| `svg/fit-page.svg` | მთელი გვერდი | `window.py a_page` | — (აიკონი არ აქვს, ზოლში ტექსტია) |
| `svg/chevron-left.svg` | ძიება: წინა | `docview.py prev_btn` | ტექსტი „‹ წინა“ |
| `svg/chevron-right.svg` | ძიება: შემდეგი | `docview.py next_btn` | ტექსტი „შემდეგი ›“ |
| `svg/arrow-up.svg` | ზემოთ | `dialogs.py MergeDialog` | ტექსტი „▲ ზემოთ“ |
| `svg/arrow-down.svg` | ქვემოთ | `dialogs.py MergeDialog` | ტექსტი „▼ ქვემოთ“ |

## შეცვლილი — 5

ახლანდელი აიკონი ცდომილია: ან სხვა ღილაკისას ემთხვევა, ან სხვა რამეს ჰგავს.

| ფაილი | ღილაკი | კოდში | ახლა |
|---|---|---|---|
| `svg/rotate-view.svg` | ხედის მობრუნება | `window.py a_rot` | 0xE7AD (= a_rr) |
| `svg/ai.svg` | AI ასისტენტი | `window.py a_ai` | 0xE99A (რობოტი) |
| `svg/stamp.svg` | შტამპი | `window.py TOOLS stamp` | 0xE8FB (✓) |
| `svg/rotate-page-right.svg` | მობრუნება მარჯვნივ | `window.py a_rr` | 0xE7AD (= a_rot) |
| `svg/rotate-page-left.svg` | მობრუნება მარცხნივ | `window.py a_rl` | 0xE7A7 (= a_undo!) |

დამატებით, აიკონის გარდა:
- **„ხედის მობრუნება“** (`a_rot`): მთავარი ტოლბარიდან „ხედი“ მენიუში გადადის. ის ფაილს არ ცვლის და ტოლბარზე `a_rr`-ს ერევა.
- **„ფერი…“** (`a_color`, `0xE790`): SVG აიკონი არ აქვს. ტოლბარზე 18px წრე იხატება არჩეული ფერით (`ink-pen` ან მომხმარებლის ფერი), 2px `surface-100` + 1px `line-strong` რგოლით. დაჭერით ჩამოიშლება ColorSwatches (`design/components/ColorSwatches`).

## იგივე დარჩა — 35

აზრით იგივეა, რაც Segoe-ში. ჩანაცვლება მხოლოდ ვარიანტ A-ზე ხდება, ერთიანი სტილისთვის.

| ფაილი | ღილაკი | კოდში | ახლა |
|---|---|---|---|
| `svg/sidebar.svg` | გვერდითი პანელი | `window.py a_side` | 0xE8A0 |
| `svg/open.svg` | გახსნა… | `window.py a_open` | 0xE8E5 |
| `svg/save.svg` | შენახვა | `window.py a_save` | 0xE74E |
| `svg/print.svg` | ბეჭდვა… | `window.py a_print` | 0xE749 |
| `svg/undo.svg` | გაუქმება | `window.py a_undo` | 0xE7A7 |
| `svg/redo.svg` | გამეორება | `window.py a_redo` | 0xE7A6 |
| `svg/pages.svg` | გვერდების მართვა | `window.py a_org` | 0xE80A |
| `svg/page-prev.svg` | წინა გვერდი | `window.py a_pgup` | 0xE70E |
| `svg/page-next.svg` | შემდეგი გვერდი | `window.py a_pgdn` | 0xE70D |
| `svg/zoom-out.svg` | დაპატარავება | `window.py a_out` | 0xE71F |
| `svg/zoom-in.svg` | გადიდება | `window.py a_in` | 0xE8A3 |
| `svg/two-pages.svg` | ორი გვერდი გვერდიგვერდ | `window.py a_two` | 0xE89A |
| `svg/search.svg` | ძიება | `window.py a_find` | 0xE721 |
| `svg/select.svg` | მონიშვნა | `window.py TOOLS select` | 0xE8B0 |
| `svg/highlight.svg` | მარკერი | `window.py TOOLS highlight` | 0xE7E6 |
| `svg/underline.svg` | ხაზგასმა | `window.py TOOLS underline` | 0xE8DC |
| `svg/strikeout.svg` | გადახაზვა | `window.py TOOLS strikeout` | 0xEDE0 |
| `svg/note.svg` | შენიშვნა | `window.py TOOLS note` | 0xE90A |
| `svg/text.svg` | ტექსტი | `window.py TOOLS text` | 0xE8D2 |
| `svg/ink.svg` | ხატვა | `window.py TOOLS ink` | 0xED63 |
| `svg/rect.svg` | ოთხკუთხედი | `window.py TOOLS rect` | 0xE7FB |
| `svg/ellipse.svg` | ელიფსი | `window.py TOOLS ellipse` | 0xEA3A |
| `svg/line.svg` | ხაზი | `window.py TOOLS line` | 0xE738 |
| `svg/arrow.svg` | ისარი | `window.py TOOLS arrow` | 0xE72A |
| `svg/image.svg` | სურათი | `window.py TOOLS image` | 0xEB9F |
| `svg/edit.svg` | ტექსტის შეცვლა | `window.py TOOLS edit` | 0xE70F |
| `svg/redact.svg` | დასაფარის მონიშვნა | `window.py TOOLS redact` | 0xE75C |
| `svg/field.svg` | ფორმის ველი | `window.py TOOLS field` | 0xE9D5 |
| `svg/signature.svg` | ხელმოწერის სურათი… | `window.py a_sigpic` | 0xE929 |
| `svg/delete.svg` | წაშლა | `window.py a_del` | 0xE74D |
| `svg/duplicate.svg` | დუბლირება | `window.py a_dup` | 0xE8C8 |
| `svg/insert-file.svg` | ჩასმა ფაილიდან… | `window.py a_ins` | 0xE710 |
| `svg/insert-blank.svg` | ცარიელი გვერდის ჩასმა | `window.py a_blank` | 0xE7C3 |
| `svg/crop.svg` | ჩამოჭრა… | `window.py a_crop` | 0xE7A8 |
| `svg/extract.svg` | ამოღება ახალ ფაილად… | `window.py a_ext` | 0xE8A7 |

## როგორ ჩაიდოს კოდში

- **ფაილები:** `design/icons-v2/svg/` → `furceli/assets/icons/`. exe-ში თან მიჰყვება, რადგან `furceli\assets` უკვე `--add-data`-შია.
- **`glyph(code, color)`-ის გვერდით** `icon(name)` ფუნქცია `theme.py`-ში ან `window.py`-ში:
  - SVG-ის ტექსტში `currentColor` → `theme.color(...)`.
  - `QSvgRenderer`-ით (PySide6-Essentials-ში არის `QtSvg`) იხატება 64×64 pixmap-ზე, `devicePixelRatio`-ის გათვალისწინებით.
  - ერთ `QIcon`-ში ემატება სამი მდგომარეობა: `Normal` + `Off` → `ink`, `Normal` + `On` → `brand-text` (checked), `Disabled` → `ink-disabled`.
  - თემის შეცვლისას თავიდან იხატება, ისე როგორც ახლა `glyph()`.
- **ზომები:** მთავარი ტოლბარი 20, მეორე 18, მენიუ და ღილაკი 16 (`setIconSize` უკვე ასეა).
- **ვარიანტი B:** `mapping.json`-დან მხოლოდ `status` = `new` / `changed`. დანარჩენი `glyph()`-ით რჩება.
- **ღილაკის ტექსტი:** ძიებისა და გაერთიანების ღილაკებს ტექსტი რჩება tooltip-ად, ზედ კი მხოლოდ აიკონია. თუ ტესტი ღილაკს ტექსტით პოულობს, `objectName`-ზე ან `toolTip`-ზე გადაიყვანე, ტესტის აზრი არ შეცვალო.

ონლაინ ვერსია (მომხმარებლისთვის): https://claude.ai/artifact/TXo6W13UQ48QiToJini7kM
