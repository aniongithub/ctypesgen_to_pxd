# ctypesgen\_to\_pxd

### Usage:

```c
ctypesgen --output-language=json /usr/include/some.h > some.json
ctypesgen_to_pxd < some.json > some.pxd

vim use_some.pxy

cythonize -i use_some.pxy
```

### Links:

* [ctypesgen](https://github.com/davidjamesca/ctypesgen)
* [cython](http://cython.org/)

### License:

* Apache License v2.0, see LICENSE
