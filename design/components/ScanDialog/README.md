# ScanDialog

„სკანის გასუფთავება“ (`scan_dialog.py`), დიდი დიალოგი ~960px. სამი სვეტი QSplitter-ში:

1. **ფაილების სია** (`et-filelist`) — სახელი + გვერდის ნომერი მარჯვნივ (`caption`); ვერწაკითხული — `danger` „ვერ იკითხება“. ქვეშ „დამატება…“ (sm) და „ამოღება“ (ghost sm).
2. **ორიგინალი** — `canvas` ფონზე ფოტო; კუთხეების ოთხკუთხედი `mark-crop` 2px + `mark-crop-fill`; სახელურები 12px თეთრი წრე `mark-crop` რგოლით. სათაური `et-label`.
3. **შედეგი** — `paper` + `shadow-page`; მუშავდება → spinner + „მუშავდება…“ `ink-muted`.

ქვედა ზოლი: „კუთხეები ავტომატურად“, „მთელი კადრი“ (sm), checkbox „სტრიქონების გასწორება“, რეჟიმი და ენა (ComboBox 28px, ლეიბლი წინ). ფუტერში მარცხნივ შენიშვნა `small`/`ink-muted`, მარჯვნივ „დახურვა“ (ghost) და „PDF-ის შექმნა…“ (primary; მუშაობისას „მუშავდება… (3)“ და disabled).

კოდში: `CornerView`-ის `#2f7de1` და `QColor(47,125,225,28)` → `mark-crop` / `mark-crop-fill`; `ResultView`-ის ჩარჩო → `shadow-page`.
