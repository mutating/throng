![logo](https://raw.githubusercontent.com/mutating/throng/develop/docs/assets/logo_1.svg)

Sometimes our programs need to execute console commands. In some cases, a command may be executed locally, while in others it may be executed in parallel across thousands of machines in the cloud. This library serves as an abstraction layer over various command execution environments, allowing you to write code once that will run anywhere. Specific execution environments are connected here as plugins (and you can even write your own!) with a unified API, and your code doesn’t need to know the internal workings of a specific plugin to run commands within it.

This library provides:

- Complete isolation of the program’s core logic from where and how commands are executed. The logic remains compact and describes the essence of the problem.
- The ability to easily swap one implementation for another. For example, you can replace local command execution with execution inside a Docker container or a cloud virtual machine without changing the code.
- Parallelization is simple. Each plugin decides for itself what level of parallelism it needs, so the main code doesn’t even need to know that it’s running in parallel.


## Table of Contents

- [**Installation**](#installation)
- [**Quick start**](#quick-start)


## Installation

You can install [`throng`](https://pypi.org/project/throng) with `pip`:

```bash
pip install throng
```

You can also use [`instld`](https://github.com/pomponchik/instld) to quickly try this package and others without installing them.


## Quick start

As an example, let's try creating a file in a directory and verify that it was created:

```python
>>> from throng import throng
>>>
>>> with throng('.')['temporary_directory'].scope as scope:
...     scope.run('ls')
...     scope.run('touch file.txt')
...     scope.run('ls')
...
SubprocessResult(id='fc1b660ab68211f196d7f6817fdcabf4', stdout='LICENSE\nREADME.md\ndocs\npyproject.toml\nrequirements_dev.txt\ntests\nthrong\nvenv\n', stderr='', returncode=0, killed_by_token=False)
SubprocessResult(id='fc1ca4f2b68211f196d7f6817fdcabf4', stdout='', stderr='', returncode=0, killed_by_token=False)
SubprocessResult(id='fc1d7602b68211f196d7f6817fdcabf4', stdout='LICENSE\nREADME.md\ndocs\nfile.txt\npyproject.toml\nrequirements_dev.txt\ntests\nthrong\nvenv\n', stderr='', returncode=0, killed_by_token=False)
```

Let’s understand this code:

- The `with ... scope` construct creates an environment for executing commands, while ensuring that this environment is cleaned up or destroyed as needed after exiting the code block.
- `scope.run()` executes the commands in the created temporary environment.
- Where exactly will the commands be executed? In this case, it’s determined by the `'temporary_directory'` string, which is actually the name of a built-in plugin. If needed, it can be replaced with the name of any other command-execution plugin you can connect, and your main code will work with it as if nothing had changed. This particular plugin creates a temporary directory on your computer, copies all files from the current directory into it, and deletes the entire directory once it’s finished.
- `'.'` means that the state of the current directory is transferred to the execution environment. You can use a different directory.
