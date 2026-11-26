# ToolButton

აიკონიანი ღილაკი ხელსაწყოთა ზოლში (QToolButton / QAction ტოლბარზე): 32×32, აიკონი 16px.

- ნაგულისხმევი: გამჭვირვალე, `ink` აიკონი; hover — `surface-200`.
- **checked** (არჩეული ხელსაწყო, „გვერდების მართვა“, „AI ასისტენტი“): `brand-soft` ფონი, `brand-text` აიკონი. ერთ ჯგუფში ერთი checked (QActionGroup).
- disabled: `ink-disabled`, ფონი არა.
- ჯგუფებს შორის `et-sep` — 1px `line`, 20px სიმაღლე.
- ყოველთვის tooltip: სახელი + კლავიში (`mono`), მაგ. „მარკერი H“.
- აიკონები: Segoe Fluent Icons (Windows 11) → Segoe MDL2 Assets; ფერი QPalette WindowText-იდან, არა მყარად ჩაწერილი — რომ მუქ თემაში შეიცვალოს.
