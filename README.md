# SublimeDelve

[Delve](https://github.com/derekparker/delve) plugin for Sublime Text 3.

Based on ideas and sources:
* [SublimeGDB](https://github.com/quarnster/SublimeGDB)
* [go-debug](https://github.com/lloiser/go-debug)
* [jsonrpctcp](https://github.com/joshmarshall/jsonrpctcp)

## Prerequisites
* [GoSublime](https://github.com/DisposaBoy/GoSublime)

## Installation
1. Using [Package Control](https://packagecontrol.io/docs/usage) Plugin (recommended)
2. Manually clone git repository [SublimeDelve](https://github.com/dishmaev/SublimeDelve) in your package directory

## Enable plugin for your project
1. On active view of window right click mouse and choose from menu Delve/Enable (not recommended, if your project file contains necessary commented lines, after execution Sublime Text will remove all commented content)
2. Manually put specific setting in project file *\<YourGoProject\>.sublime-project*
```
"settings":
{
  ...
  "delve_enable": true
  ...
}
```

## License
SublimeDelve are released under the MIT license. See [LICENSE](https://github.com/dishmaev/SublimeDelve/blob/master/LICENSE)