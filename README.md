![throng logo](https://raw.githubusercontent.com/mutating/throng/develop/docs/assets/logo_1.svg)

`throng` provides a common API for running commands in different environments, from a local directory to cloud infrastructure. Plugins handle the details of each environment, so your application can switch between them without changing how it submits commands or reads results. You can use the built-in plugins, install third-party plugins, or write your own.

This library provides:

- Separation of application logic from the details of command execution.
- Interchangeable execution environments. For example, a plugin can run commands in a Docker container or a cloud virtual machine through the same API as local execution.
- Plugin-managed concurrency limits. Each plugin controls when resources are available to create an execution environment.


## Table of contents

- [**Installation**](#installation)
- [**Quick start**](#quick-start)
- [**Why?**](#why)
- [**Key concepts**](#key-concepts)
- [**Isolates and command execution**](#isolates-and-command-execution)
- [**Managers**](#managers)
- [**Plugins**](#plugins)


## Installation

You can install [`throng`](https://pypi.org/project/throng) with `pip`:

```bash
pip install throng
```

You can also use [`instld`](https://github.com/pomponchik/instld) to quickly try this package and others without installing them.


## Quick start

Let's create a file in a temporary copy of the current directory and verify that it exists:

```python
>>> from throng import throng
>>>
>>> with throng('.')['temporary_directory'].scope as isolate:
...     isolate.run('ls')
...     isolate.run('touch file.txt')
...     isolate.run('ls')
...
SubprocessResult(id='fc1b660ab68211f196d7f6817fdcabf4', stdout='LICENSE\nREADME.md\ndocs\npyproject.toml\nrequirements_dev.txt\ntests\nthrong\nvenv\n', stderr='', returncode=0, killed_by_token=False)
SubprocessResult(id='fc1ca4f2b68211f196d7f6817fdcabf4', stdout='', stderr='', returncode=0, killed_by_token=False)
SubprocessResult(id='fc1d7602b68211f196d7f6817fdcabf4', stdout='LICENSE\nREADME.md\ndocs\nfile.txt\npyproject.toml\nrequirements_dev.txt\ntests\nthrong\nvenv\n', stderr='', returncode=0, killed_by_token=False)
```

Here's how it works:

- The `with ... as isolate` statement creates an execution environment and cleans it up when the block exits.
- `isolate.run()` executes a command in that environment.
- `'temporary_directory'` selects a built-in plugin that copies files from the source directory into a temporary directory on your computer. It deletes the temporary directory when the block exits. Select a different installed plugin to use another execution environment.
- `'.'` selects the current directory as the source. You can pass a different directory instead.


## Why?

Commands can run on your computer, on a remote machine accessed via SSH, in a Docker container or virtual machine, or in a serverless environment such as AWS Lambda. Each option has different resource requirements and isolation properties.

Two useful dimensions for comparing execution environments are parallelism and isolation. Parallelism is limited by available resources, such as memory and processing power. Isolation determines how much executions can affect one another. Running commands in separate directories offers little isolation, while virtual machines and serverless environments can provide stronger boundaries.

![Comparison of command execution environments by parallelism and isolation](https://raw.githubusercontent.com/mutating/throng/develop/docs/assets/illustration_1.png)
> ⓘ This illustration shows a rough comparison, not measured limits. Actual parallelism and isolation depend on the implementation and configuration.

Both parallelism and isolation come at a cost. Starting a virtual machine, for example, generally takes more time and memory than starting a local process. The right choice depends on your workload, isolation requirements, and budget.

Execution systems often expose APIs tied to their infrastructure. Supporting several of them directly can spread infrastructure-specific code throughout your application and duplicate logic for starting commands, collecting results, and releasing resources.

`throng` puts these details behind a common interface using the [`pristan`](https://github.com/mutating/pristan) plugin system. Choose a plugin that offers the balance of execution cost, parallelism, and isolation best suited to your needs.

The plugin manages execution resources and isolation; your application submits commands and receives results. This lets you develop your application locally and use another environment through the same API, provided that environment supports your commands.


## Key concepts

`throng` has four key concepts:

- Plugins.
- Isolates.
- Isolate managers.
- Initial state.

Each plugin provides an execution method through a common interface. Your application selects a plugin without needing to manage its internal execution details.

The `throng` object can be imported and called like a regular function. In `pristan` terminology, this object is a *slot*. Calling it returns a dictionary mapping plugin names to manager objects. With only the built-in plugins installed, the result looks like this:

```python
from throng import throng

managers = throng('.')
print(managers)
#> {'local': LocalManager('.'), 'temporary_directory': TemporaryDirectoryManager('.')}
```

> ↑ We pass `'.'` to refer to the current directory. Each plugin determines how to use that directory when creating isolates.

`throng` comes with two built-in plugins. Let's retrieve the manager provided by the local execution plugin:

```python
manager = managers['local']
```

Managers share a common API for reading initial state and creating isolates. They also provide convenience methods that manage an isolate's lifecycle for you.

An isolate represents an execution environment and runs commands in it. You can manage its lifecycle explicitly or let a manager handle creation and cleanup.

To create an isolate, read the initial state and pass it to the manager's `get()` method:

```python
state = manager.read()
isolate = manager.get(state)
```

> ⓘ State is a `bytes` object whose contents and format are defined by the plugin. Treat it as opaque data unless you are writing code for a specific plugin and its documented state format. State from one plugin is not necessarily compatible with another.

Once the isolate has been created, you can try running a command in it:

```python
result = isolate.run('ls')
print(result.stdout)
#> LICENSE
#> README.md
#> docs
#> pyproject.toml
#> requirements_dev.txt
#> tests
#> throng
#> venv
```

> ↑ This example was run in the `throng` project directory. Your `ls` output will depend on the contents of your directory.

When you no longer need an isolate, call its `kill()` method:

```python
isolate.kill()
```

An isolate may hold resources until it is destroyed. For example, if it represents a virtual machine, cleanup may need to release the memory and other resources allocated to that machine. The plugin handles the details of releasing these resources.

To avoid managing an isolate's lifecycle manually, use a context manager:

```python
with manager.scope as isolate:
    isolate.run('touch x.txt')
    isolate.run('touch y.txt')
    isolate.run('touch z.txt')
    print(isolate.run('ls').stdout)
#> LICENSE
#> README.md
#> docs
#> pyproject.toml
#> requirements_dev.txt
#> tests
#> throng
#> venv
#> x.txt
#> y.txt
#> z.txt
```

The context manager calls `kill()` when the block exits, including when an exception is raised.

If you only need to run one command, pass it directly to the manager. The manager creates an isolate, runs the command, destroys the isolate, and returns the result:

```python
print(manager.run('ls').stdout)
#> LICENSE
#> README.md
#> docs
#> pyproject.toml
#> requirements_dev.txt
#> tests
#> throng
#> venv
#> x.txt
#> y.txt
#> z.txt
```

Reading state and creating an isolate can be expensive. If several commands need the same environment, reuse an isolate or pass the commands to `manager.chain()`.

The following sections describe command execution, manager APIs, and the built-in plugins, along with how to install additional plugins.


## Isolates and command execution

Isolates run commands and return results in a common format. The selected plugin determines how and where those commands execute.

To obtain an isolate, read the initial state and pass it to the manager:

```python
from throng import throng

managers = throng('.')
manager = managers['temporary_directory']
state = manager.read()
isolate = manager.get(state)
```

A command is passed as a string. The plugin determines how to interpret it and which programs and command syntax are available. Check the plugin's documentation before relying on shell features or operating-system-specific commands, and check the result of each command.

Each command returns a result object with the following fields:

- `success` (`bool`): whether the command completed successfully.
- `returncode` (`int | None`): the command's [exit status](https://en.wikipedia.org/wiki/Exit_status), or `None` if the command did not start.
- `stdout` (`str | None`): captured standard output, or `None` if the command was not executed.
- `stderr` (`str | None`): captured standard error output, or `None` if the command was not executed.

These fields are part of the common API. Plugins may add fields, but portable application code should rely only on the fields listed above.

You can also pass a cancellation token from the `cantok` library to an isolate. The token signals when command execution should stop. For example, a timeout token requests cancellation after a specified interval:

```python
from cantok import TimeoutToken

print(isolate.run('python -c "import time; time.sleep(1000)"', token=TimeoutToken(0.1)))
#> SubprocessResult(id='01fcdbacb6d911f1808df6817fdcabf4', stdout='', stderr='', returncode=-9, killed_by_token=True)
```

Cancelling a token requests early termination of the command running in the isolate, but does not guarantee it. The plugin determines whether and how to respond to cancellation.

Use `chain()` to run a sequence of commands in the same isolate:

```python
print(
    isolate.chain(
        'touch x.txt',
        'touch y.txt',
        'touch z.txt',
    )
)
#> [SubprocessResult(id='4f0e1a3eb75411f18829f6817fdcabf4', stdout='', stderr='', returncode=0, killed_by_token=False), SubprocessResult(id='4f10c432b75411f18829f6817fdcabf4', stdout='', stderr='', returncode=0, killed_by_token=False), SubprocessResult(id='4f115172b75411f18829f6817fdcabf4', stdout='', stderr='', returncode=0, killed_by_token=False)]
```

When you no longer need a particular isolate, call its `kill()` method:

```python
isolate.kill()
```

Do not run commands in an isolate after destroying it; doing so may raise an exception. The time required by `kill()` depends on the plugin and may include network calls or other expensive operations. Plugin authors should keep cleanup as fast as practical.

If your application exits without cleaning up an isolate, allocated resources may remain in use. `throng` cannot guarantee cleanup after abnormal termination. Consult the plugin's documentation for any infrastructure-specific cleanup mechanisms.


## Managers

Managers create isolates and provide methods for managing their lifecycle. A manager can also limit concurrency by controlling when isolates are created and which resources they share.

For example, a manager might maintain a pool of workers and create an isolate only when a worker becomes available. A request for a new isolate may block until the necessary resources are available. A manager can also use a mutex or semaphore to limit local concurrency, or adjust resource requests based on feedback from the execution system. The plugin handles these details.

Operation latency depends on the selected plugin. Creating an isolate or running a command may involve starting a local process, waiting for capacity, or contacting a remote service, so application code should not assume these operations are immediate.

There are two ways to submit commands:

- Obtain individual isolates and work with them: the *open* approach.
- Pass commands directly to a manager, which handles the isolates internally: the *closed* approach.

First, obtain a manager:

```python
from throng import throng

managers = throng('.')
manager = managers['local']
```

With the open approach, you manage the isolate explicitly:

```python
state = manager.read()
isolate = manager.get(state)
try:
    isolate.run('ls')
finally:
    isolate.kill()
```

With the closed approach, the manager handles creation and cleanup:

```python
manager.run('ls')
```

The `chain()` method executes a sequence of commands in the same isolate and returns their results in order. The base implementation runs the commands sequentially. You can call it on an isolate:

```python
with manager.scope as isolate:
    isolate.chain(
        'touch x.txt',
        'touch y.txt',
        'touch z.txt',
    )
```

Or call it directly on the manager, which creates one isolate for the entire sequence and destroys it afterward:

```python
manager.chain(
    'touch x.txt',
    'touch y.txt',
    'touch z.txt',
)
```

Use the closed approach for a single command or a fixed sequence of commands. Work with an isolate directly when later commands depend on earlier results, when you need to reuse an environment, or when you want to create isolates from a saved state. Prefer `manager.scope` when the isolate's lifetime fits within a single block.


## Plugins

`throng` uses `pristan` plugins to provide interchangeable execution environments.

`throng` includes two built-in plugins:

- A plugin for running commands **locally**.
- A plugin for running commands in **temporary directories**.

Both plugins run commands on your computer. They are useful for local workflows and testing, but do not provide a security sandbox or distribute execution across machines.

The local execution plugin runs commands directly in the directory you specify:

```python
from throng import throng

managers = throng('.')
manager = managers['local']

print(manager.run('ls').stdout)
#> LICENSE
#> README.md
#> docs
#> pyproject.toml
#> requirements_dev.txt
#> tests
#> throng
#> venv
```

For the local plugin, `manager.read()` returns `b''`, and `manager.get(state)` does not restore a snapshot. Commands operate directly on the original files in the specified directory, so any changes persist after the isolate is destroyed.

For the temporary directory plugin, `manager.read()` packs files from the specified source directory into a tar archive, preserving their relative paths. Empty directories are not included. Calling `manager.get(state)` creates a temporary directory and extracts the supplied archive into it. Commands run with that directory as their working directory, and `kill()` deletes it. The directory provides a separate workspace, but does not restrict commands from accessing other files on your computer.

Select this plugin with the `'temporary_directory'` key:

```python
managers = throng('.')
manager = managers['temporary_directory']
```

For stronger isolation or distributed execution, install an additional plugin that provides those capabilities. A `throng` plugin is a Python package registered through `pristan`. Install it with `pip` or `uv`; for example, replacing `plugin-name` with the package's actual name:

```bash
pip install plugin-name
```

Once installed, the plugin is discovered automatically. Select its manager by the name documented by the plugin:

```python
managers = throng('.')
print(managers)
#> {'local': LocalManager('.'), 'temporary_directory': TemporaryDirectoryManager('.'), 'plugin_name': ThirdPartyManager('.')}

manager = managers['plugin_name']
```

The manager supports the same API as the built-in plugins. Consult the plugin's documentation for supported commands, isolation guarantees, concurrency limits, and other environment-specific behavior.
