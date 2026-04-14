import heapq

# Utils ----------------------------------------------------------------

def cached(fn):
    fn_name = fn.__name__
    def wrapper(self):
        class_name = self.__class__.__name__
        full_name = f"{class_name}_{fn_name}"

        # Safely fetch the cache dictionary, or create it if it doesn't exist
        cache_dict = getattr(self, "_cache_", None)
        if cache_dict is None:
            cache_dict = {}
            setattr(self, "_cache_", cache_dict)

        try:
            return cache_dict[full_name]
        except KeyError:
            result = fn(self)
            cache_dict[full_name] = result
            return result

    return wrapper

# Priority queue ----------------------------------------------------------------

class PrioritySet(object):

    def __init__(self, iterable = None):

        if iterable is not None:
            self._heap = list(iterable)
            self._contained = set(iterable)
            heapq.heapify(self._heap)
        else:
            self._heap = []
            self._contained = set()
        
        self._removed   = set()

    def _update(self):
        while len(self._heap) > 0 and self._heap[0] in self._removed:
            self._removed.remove(heapq.heappop(self._heap))

    def __contains__(self, object):
        return object in self._contained

    def add(self, object):
        if object in self._contained: return False
        if object in self._removed:
            self._removed.remove(object)
            self._contained.add(object)
            return True
        
        self._update()
        self._contained.add(object)
        heapq.heappush(self._heap, object)
        return True

    def peek(self):
        self._update()
        return self._heap[0]

    def pop(self):
        self._update()
        result = heapq.heappop(self._heap)
        self._contained.remove(result)
        return result
    
    def remove(self, object):
        if object not in self._contained: return False
        self._removed.add(object)
        self._contained.remove(object)
        return True
    
    def __len__(self):
        return len(self._heap)
