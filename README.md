![logo](https://raw.githubusercontent.com/mutating/throng/develop/docs/assets/logo_1.svg)

Sometimes our programs need to execute console commands. In some cases, a command may be executed locally, while in others it may be executed in parallel across thousands of machines in the cloud. This library serves as an abstraction layer over various command execution environments, allowing you to write code once that will run anywhere. Specific execution environments are connected here as plugins (and you can even write your own!) with a unified API, and your code doesn’t need to know the internal workings of a specific plugin to run commands within it.

This library provides:

- Complete isolation of the program’s core logic from where and how commands are executed. The logic remains compact and describes the essence of the problem.
- The ability to easily swap one implementation for another. For example, you can replace local command execution with execution inside a Docker container or a cloud virtual machine without changing the code.
- Parallelization is simple. Each plugin decides for itself what level of parallelism it needs, so the main code doesn’t even need to know that it’s running in parallel.


## Table of Contents

- [**Installation**](#installation)
- [**Quick start**](#quick-start)
- [**Why?**](#why)


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


## Why?

There are many places and ways to run console commands. You can almost certainly run such a command in some way on the device you’re currently using to read this text. You can also connect to a remote device via SSH and run the command there. You can create a Docker container or a local virtual machine and pass the command to it for execution. Commands can also be executed in serverless environments like AWS Lambda. Of course, we won’t list every possible option here.

There are two independent axes along which we can evaluate different methods of executing commands. First, different methods vary in their degree of parallelism. For example, your computer almost certainly has limited memory and processing power, so we cannot increase parallelism indefinitely. Second, different methods have different levels of isolation for command execution. For instance, if you run commands in different directories on your computer, such isolation can only be described as very limited—processes can easily interact with one another and interfere with each other. If, on the other hand, you run commands via serverless environments, they are usually fairly well isolated from one another, and it is much more difficult for them to interact with each other.

Importantly, both parallelism and isolation come at a cost. For example, if you decide to thoroughly isolate a command when running it on your computer by using virtual machines, you’ll need much more memory and time to start executing the command than you would with a “naive” execution of the command. That’s why it’s good to have a choice of different options, so you can select the one that best fits your specific needs and the price you’re willing to pay for it.

Most systems that offer you various ways to execute commands are typically tied to a specific infrastructure—whether physical or software-based—whose position on the two axes described above is fixed. If you want to write your own logic on top of such systems, your code will generally contain duplicate components. And with every new way of executing commands you add, your codebase will become bloated.

`throng` solves this problem. Various command execution systems are abstracted here into a separate layer that can be easily swapped out for another using the modern [`pristan`](https://github.com/mutating/pristan) plugin system. You can enable the plugin that is most optimal for you in terms of operation cost, while also offering the right balance on the axes of parallelism and execution isolation. Now you don’t need to rewrite or bloat your program when you want to add another way to execute code—just select the appropriate plugin and connect it.
