# throng

Sometimes our programs need to execute console commands. In some cases, a command may be executed locally, while in others it may be executed in parallel across thousands of machines in the cloud. This library serves as an abstraction layer over various command execution environments, allowing you to write code once that will run anywhere. Specific execution environments are connected here as plugins (and you can even write your own!) with a unified API, and your code doesn’t need to know the internal workings of a specific plugin to run commands within it.

This library provides:

- Complete isolation of the program’s core logic from where and how commands are executed. The logic remains compact and describes the essence of the problem.
- The ability to easily swap one implementation for another. For example, you can replace local command execution with execution inside a Docker container or a cloud virtual machine without changing the code.
- Parallelization is simple. 


## Known limitations

`load()` preserves excluded paths by temporarily moving existing isolate
contents through a backup directory created in the platform's default
temporary location. If an isolate is placed on a different filesystem from
that temporary location, excluded special filesystem entries such as POSIX
named pipes are not guaranteed to be restored safely. Keep such isolates on
the same filesystem as the platform temporary directory when excluded special
entries must be preserved.

## Issues



- Cancellation during `dump()` and `load()` is currently observed between files,
not while the contents of one large regular file are being copied. A future
implementation should copy large file payloads in chunks and call the
operation cancellation token between chunks. Benchmarks with a never-cancelled
`SimpleToken` indicate that **256 KiB** is the recommended default chunk size:
it keeps cancellation response latency low while adding no measurable
regression for the supported tar compression paths.

- Add reprs to all basic classes.

- Add size limits and some reactions to overflow.

- Add more optional backends.

- Log information about file sizes.

- Add to pristan possibility to declare signature as a list (not only str as now).

- Add to dirstree apply method, and sorting, and non-pass non-file mode, and fix the tree before using. And use it all here after. And add watchdog support (https://github.com/gorakhargosh/watchdog/).
