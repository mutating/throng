from typing import List

from pristan import slot


@slot(max=1, entrypoint_group='pupupu')
def get_runner() -> List[AbstractIsolate]:
    return [DirectoryIsolate()]
