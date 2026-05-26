from typing import Dict

from emptylog import EmptyLogger, LoggerProtocol
from pristan import slot

from throng.abstracts.throng import AbstractThrong


@slot(entrypoint_group='throng')
def throngs(logger: LoggerProtocol = EmptyLogger()) -> Dict[str, AbstractThrong]:  # type: ignore[empty-body]  # noqa: B008
    """
    Resolve registered throng plugins, passing them the inherited logger.

    Pristan supplies this function body at runtime and discovers third-party
    providers from the ``throng`` entry-point group.
    """


__all__ = [
    'throngs',
]
