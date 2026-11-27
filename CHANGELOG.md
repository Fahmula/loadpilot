# Changelog

## [1.5.0](https://github.com/Fahmula/loadpilot/compare/v1.4.1...v1.5.0) (2026-11-27)


### Features

* email invoices to brokers from the load page ([#40](https://github.com/Fahmula/loadpilot/issues/40)) ([b7616cd](https://github.com/Fahmula/loadpilot/commit/b7616cdbbb3ad119be123522687d7d2779753534))
* export loads as CSV and download a full backup ([#39](https://github.com/Fahmula/loadpilot/issues/39)) ([c1fed9d](https://github.com/Fahmula/loadpilot/commit/c1fed9dfd06b98569c83bc289e190d73e7564867))
* let other apps use the API with a token ([#37](https://github.com/Fahmula/loadpilot/issues/37)) ([79ffcef](https://github.com/Fahmula/loadpilot/commit/79ffcefa008ab094c225f222b2a636013d7f7478))
* track expenses and show profit per mile ([#41](https://github.com/Fahmula/loadpilot/issues/41)) ([5245b5a](https://github.com/Fahmula/loadpilot/commit/5245b5a727af3d81aea4dffdb0aa88fcb78c17f1))


### Bug Fixes

* apply each database migration all or nothing ([#35](https://github.com/Fahmula/loadpilot/issues/35)) ([d79ccf7](https://github.com/Fahmula/loadpilot/commit/d79ccf7b9fd0aba8a7cda4c73ff4eeb037a70426))
* delete a load's documents along with the load ([#33](https://github.com/Fahmula/loadpilot/issues/33)) ([0fd9fc6](https://github.com/Fahmula/loadpilot/commit/0fd9fc61b2f74009279579fd94f42f02e7cce1e8))
* don't point Outlook users at a server that may refuse them ([#43](https://github.com/Fahmula/loadpilot/issues/43)) ([4ba28ce](https://github.com/Fahmula/loadpilot/commit/4ba28cef7571e79fd0b41dd7d0acced279910a99))
* run the container as a regular user, not root ([#36](https://github.com/Fahmula/loadpilot/issues/36)) ([1ce54a3](https://github.com/Fahmula/loadpilot/commit/1ce54a329b99149fa367603408749232e75dec6b))
* serve htmx from LoadPilot instead of unpkg ([#34](https://github.com/Fahmula/loadpilot/issues/34)) ([9d342c7](https://github.com/Fahmula/loadpilot/commit/9d342c7344cc56fabe7b2ffe433965de2ca80c6a))

## [1.4.1](https://github.com/Fahmula/loadpilot/compare/v1.4.0...v1.4.1) (2026-11-26)


### Bug Fixes

* count what brokers actually paid in revenue and RPM ([#30](https://github.com/Fahmula/loadpilot/issues/30)) ([a03f13c](https://github.com/Fahmula/loadpilot/commit/a03f13c4a4fe76e52aebf65dda0de01645ffa617))
* refuse a BOL that can't be read instead of invoicing without it ([#28](https://github.com/Fahmula/loadpilot/issues/28)) ([8bbadac](https://github.com/Fahmula/loadpilot/commit/8bbadac229a1af665eed023831a1fb5a07b958a8))
* secure the session cookie on HTTPS and warn about an untrusted proxy ([#32](https://github.com/Fahmula/loadpilot/issues/32)) ([eecefdf](https://github.com/Fahmula/loadpilot/commit/eecefdf247326301d70caeaaebad604d17f4d2fe))
* stop stale pages from moving a load backwards ([#29](https://github.com/Fahmula/loadpilot/issues/29)) ([00183c4](https://github.com/Fahmula/loadpilot/commit/00183c4b16dcfa5b62716467617970cc54575cc1))

## [1.4.0](https://github.com/Fahmula/loadpilot/compare/v1.3.0...v1.4.0) (2026-11-23)


### Features

* attach the signed BOL to the invoice PDF ([#25](https://github.com/Fahmula/loadpilot/issues/25)) ([2e969ec](https://github.com/Fahmula/loadpilot/commit/2e969ec6b85b7e9eaa70f2adc7dacb06cb3e6576))
* put MC and USDOT numbers on invoices ([#24](https://github.com/Fahmula/loadpilot/issues/24)) ([ce95d94](https://github.com/Fahmula/loadpilot/commit/ce95d947ba3ce58cfafddeed9c9875c1b6058519))
* redesign the UI in dark navy and amber ([#21](https://github.com/Fahmula/loadpilot/issues/21)) ([ebac154](https://github.com/Fahmula/loadpilot/commit/ebac15492d4e1f4ac0436b1c49715be285284346))


### Bug Fixes

* draw the invoice's Total Due as one bar ([#23](https://github.com/Fahmula/loadpilot/issues/23)) ([2130b98](https://github.com/Fahmula/loadpilot/commit/2130b98bfb306c2cb062379b05f5a34fd64fa561))
