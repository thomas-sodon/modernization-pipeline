# Third-party material in core/

## Jackcess (behaviour reference for vbacompat.py)

| | |
|---|---|
| Name | Jackcess, the expression engine `io.github.spannm.jackcess.impl.expr` (`FormatUtil`, `NumberFormatter`, `DefaultFunctions`, `DefaultDateFunctions`) and `io.github.spannm.jackcess.expr` (`TemporalConfig`, `NumericConfig`) |
| Version | 5.1.7 (Maven `io.github.spannm:jackcess:5.1.7`) |
| URL | https://github.com/spannm/jackcess |
| Licence | Apache-2.0 (Apache License, Version 2.0) |
| Copyright | Copyright (c) 2016 James Ahlborn; Copyright (c) 2024 Markus Spann |
| Used in | `vbacompat.py`: the behaviour of Access's `Format` (named formats, custom date, number and text formats, how the format type is chosen, Null handling, half-even rounding), the United States date and number settings, and the number-to-text rule (15 significant digits for a Double) |

No Jackcess code is included. `vbacompat.py` is a new Python implementation, in its own
structure, of the behaviour that Jackcess's Format code implements in Java; its tests
take their expected values from Microsoft's documentation and from Jackcess's
behaviour. Where Microsoft documents a different result (minutes after an hour in a
custom date format, a two-digit exponent in the Scientific format), `vbacompat.py`
follows Microsoft.

Notice for the work this module is derived from:

```
Copyright (c) 2016 James Ahlborn
Copyright (c) 2024 Markus Spann

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
