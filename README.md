# SublimeDelve

[Delve](https://github.com/derekparker/delve) plugin for Sublime Text 3.

Based on ideas and sources:
* [SublimeGDB](https://github.com/quarnster/SublimeGDB)
* [go-debug](https://github.com/lloiser/go-debug)
* [jsonrpctcp](https://github.com/joshmarshall/jsonrpctcp)

## Prerequisites
* [GoSublime](https://github.com/DisposaBoy/GoSublime)

## Installation
* Using [Package Control](https://packagecontrol.io/docs/usage) Plugin (recommended)
* Manually clone git repository [SublimeDelve](https://github.com/dishmaev/SublimeDelve) in your package directory

## Project activation
* On active view of window right click mouse and choose from menu Delve/Enable
* Manually put specific setting in *\<YourGoProject\>.sublime-project* file
```
"settings":
{
  ...
  "delve_enable": true
  ...
}
```

## License
SublimeDelve are released under the MIT license. See [LICENSE](https://github.com/dishmaev/SublimeDelve/blob/develop/LICENSE)