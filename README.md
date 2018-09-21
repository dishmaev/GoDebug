# GoDebug

[Delve](https://github.com/derekparker/delve) plugin for Sublime Text 3.

Based on ideas and sources:
* [SublimeGDB](https://github.com/quarnster/SublimeGDB)
* [go-debug](https://github.com/lloiser/go-debug)
* [jsonrpctcp](https://github.com/joshmarshall/jsonrpctcp)

## Prerequisites
* [GoSublime](https://github.com/DisposaBoy/GoSublime)

## Installation
1. Using [Package Control](https://packagecontrol.io/docs/usage) Plugin
2. Manually clone git repository [GoDebug](https://github.com/dishmaev/GoDebug) in your package directory

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

## Usage
See [the default key bindings](https://github.com/dishmaev/GoDebug/blob/master/Default.sublime-keymap), [the default mouse map](https://github.com/dishmaev/GoDebug/blob/master/Default.sublime-mousemap) and [the sample setting](https://github.com/dishmaev/GoDebug/blob/master/GoDebug.sublime-settings).

In short:
* If you have multiple projects, you most likely want to put project specific setting in your project file, with a prefixed "godebug_"
* If you have multiple executables in the same project, you can add a "godebug_executables" setting to your project settings, and add an entry for each executable's settings
* Toggle breakpoints with Alt+F9
* Launch with F5
* Next with F6
* Step into with F7
* Step out with Shift+F7
* Click on the appropriate line in the Delve Stacktrace view to go to that stack frame. Deactivated by default, see [the mouse map](https://github.com/dishmaev/GoDebug/blob/master/Default.sublime-mousemap) for details
* Click a variable in the Delve Variables view to show its children (if available).Deactivated by default, see [the mouse map](https://github.com/dishmaev/GoDebug/blob/master/Default.sublime-mousemap) for details
* You can also access some commands by right clicking in any view

## License
GoDebug are released under the MIT license. See [LICENSE](https://github.com/dishmaev/GoDebug/blob/master/LICENSE)
