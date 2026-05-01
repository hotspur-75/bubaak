""" Tasks for Predictive Dynamic Program Splitting """
import sys
from os.path import join as pathjoin
import time

from bbk.dbg import dbg, print_stdout
from bbk.task.continuationtask import ContinuationTask
from bbk.task.result import TaskResult
from bbk.verdict import Verdict
from bbk.compiler import CompilationUnit

from .split import (
    DynamicSplitResult, ParallelCompose, SplitTask, 
    ComposeResults, IdentityTask, cpa_merge
)
from .verifiers import SoftwareVerifier, task_result_is_conclusive, found_bug

import re

import workflows.flows.split as split_module

def _predictive_splits(self):
    """Custom split extractor that uses duck-typing instead of strict type-checking"""
    if not self._splits:
        unknown_results = [res for res in self.output if isinstance(res.output[0], Verdict) and res.output[0].is_unknown()]
        splits = []
        for unknown_result in unknown_results:
            task = unknown_result.task
            # Look for the inputfile method instead of asserting 'CheckTask'
            if hasattr(task, 'inputfile'):
                splits.append(task.inputfile())
            else:
                raise AssertionError(f"Task {task} has no inputfile() method")
        self._splits = splits
    return self._splits

# Override the framework's strict type-checker
split_module.DynamicSplitResult.splits = _predictive_splits

def _predictive_next_split_parent(task):
    """
    Custom split parent resolver that supports PredictiveSplitAndCheckTask
    and SequentialCheckTask by looking for attributes instead of hardcoded types.
    """
    # If the task itself is a splitting node, return the node above it
    if "SplitAndCheckTask" in task.__class__.__name__:
        return getattr(task, "split_parent", None)
    
    # Traverse up to find the nearest task that has a valid split_parent link
    while task:
        # If we hit our SequentialCheckTask (or any task with a linked parent), return it
        if hasattr(task, "split_parent") and task.split_parent is not None:
            return task.split_parent
            
        if hasattr(task, "parent") and callable(task.parent):
            task = task.parent()
        else:
            break
            
    return None

# Override the framework's strict type-checker with our dynamic one
split_module._next_split_parent = _predictive_next_split_parent

import sys
import joblib
import pandas as pd
from bbk.env import get_env

# ---------------------------------------------------------
# 1. CROSS-DIRECTORY IMPORTS & PATH SETUP
# ---------------------------------------------------------
# Inject the pycpa directory into sys.path so we can import the extractor
pycpa_dir = pathjoin(get_env().srcdir, "program-splitter", "pycpa")
if pycpa_dir not in sys.path:
    sys.path.insert(0, pycpa_dir)

try:
    # Import the metric extraction function from your pycpa script
    from extract_svcomp_metrics import get_metrics_for_file
except ImportError as e:
    print(f"[!] Critical Import Error: Could not load pycpa metric extractor. {e}")
    get_metrics_for_file = None

# Remove the path to keep sys.path clean
sys.path.pop(0)

# ---------------------------------------------------------
# 2. GLOBAL MODEL CACHE (Prevents massive I/O lag)
# ---------------------------------------------------------
_CACHED_MODELS = None

def get_loaded_models():
    """Loads the 4 RF models from disk exactly once and caches them in RAM."""
    global _CACHED_MODELS
    if _CACHED_MODELS is not None:
        return _CACHED_MODELS
    
    from bbk.dbg import dbg
    dbg("[PREDICTOR] Initializing AI Models from disk to RAM cache...", color="yellow")
    
    _CACHED_MODELS = {}
    verifiers = ['pa', 'ki', 'se', 'bmc']
    for v in verifiers:
        model_path = pathjoin(pycpa_dir, f'predictor_{v}.joblib')
        _CACHED_MODELS[v] = joblib.load(model_path)
        
    return _CACHED_MODELS

# ---------------------------------------------------------
# 3. THE HEURISTIC PREDICTOR FUNCTION
# ---------------------------------------------------------
def heuristic_predictor(inputfile, verifiers: list, depth: int, lineage_state: dict = None):
    """
    An AI-driven, CFA-based predictor.
    Extracts topological features, evaluates Random Forest models, 
    applies lineage state/depth scaling, and dynamically inserts the 'split' token.
    """
    # 0. Initialize state safely to avoid cross-contamination
    if lineage_state is None:
        lineage_state = {'punish': [], 'reward': []}
        
    from bbk.dbg import dbg
    dbg(f"[PREDICTOR] AI-driven telemetry for {inputfile.path} at Depth {depth}...", color="cyan")
    if lineage_state.get('punish') or lineage_state.get('reward'):
        dbg(f"  -> Lineage Memory Active: Punishing {lineage_state.get('punish')}, Rewarding {lineage_state.get('reward')}", color="magenta")
    
    # Bubaak Verifier Mapping (Index depends on how you instantiated them)
    # Ensure this matches the list passed by your workflow!
    v_map = {'pa': verifiers[0], 'ki': verifiers[1], 'se': verifiers[2], 'bmc': verifiers[3]}
    
    try:
        if get_metrics_for_file is None:
            raise ImportError("Metric extractor was not loaded.")

        # 1. Fetch AI Models (instantaneous if already cached)
        models = get_loaded_models()

        # 2. Extract topological metrics from the raw .c file via PyCPA
        metrics = get_metrics_for_file(inputfile.path)
        
        if not metrics or metrics.get('status') == 'ERROR':
            dbg("[PREDICTOR] Parsing failed (likely sequentialized benchmark). Using safe fallback.", color="red")
            return [v_map['se'], v_map['pa'], 'split', v_map['ki'], v_map['bmc']]

        # 3. Convert dict to exactly the format expected by scikit-learn
        import pandas as pd
        X_input = pd.DataFrame([metrics])

        # Dynamically fetch the exact feature names the model memorized during training
        expected_features = models['se'].feature_names_in_
        
        # Safety Check: If the PyCPA extractor missed a column, fill it with 0.0
        for feat in expected_features:
            if feat not in X_input.columns:
                X_input[feat] = 0.0
                
        # Reorder the dataframe columns to perfectly match the model's training order
        X_input = X_input[expected_features]

        # 4. Predict raw success probabilities
        raw_probs = {}
        for v_name, model in models.items():
            raw_probs[v_name] = model.predict_proba(X_input)[0][1]
            
        dbg(f"  -> Raw Confidence: SE:{raw_probs['se']:.2f}, PA:{raw_probs['pa']:.2f}, KI:{raw_probs['ki']:.2f}, BMC:{raw_probs['bmc']:.2f}", color="yellow")

        # 5. Apply Stateful Memory and Advanced Depth Scaling
        PENALTY_MULTIPLIER = 0.50
        REWARD_MULTIPLIER = 1.20
        DEPTH_BOOST_FACTOR = 1.05    # Boost underapproximators in deep/linear code
        DEPTH_PENALTY_FACTOR = 0.90  # Penalize overapproximators due to broken invariants
        T_FLOOR = 0.15               # Absolute minimum threshold
        
        adjusted_probs = {}
        for v_name, raw_prob in raw_probs.items():
            modifier = 1.0
            
            # Lineage Feedback Loop
            if v_name in lineage_state.get('punish', []):
                modifier *= PENALTY_MULTIPLIER
            if v_name in lineage_state.get('reward', []):
                modifier *= REWARD_MULTIPLIER
                
            # Depth Topology Scaling
            if v_name in ['se', 'bmc']:
                modifier *= (DEPTH_BOOST_FACTOR ** depth)
            elif v_name in ['pa', 'ki']:
                modifier *= (DEPTH_PENALTY_FACTOR ** depth)
                
            # Calculate final adjusted probability (clamped between 0.0 and 1.0)
            adjusted_probs[v_name] = min(1.0, max(0.0, raw_prob * modifier))

        dbg(f"  -> Adj Confidence: SE:{adjusted_probs['se']:.2f}, PA:{adjusted_probs['pa']:.2f}, KI:{adjusted_probs['ki']:.2f}, BMC:{adjusted_probs['bmc']:.2f}", color="green")

        # 6. Dynamic Thresholding
        sorted_verifiers = sorted(adjusted_probs.items(), key=lambda item: item[1], reverse=True)
        max_confidence = sorted_verifiers[0][1] if sorted_verifiers else 0.0
        
        # Threshold drops as depth increases, anchored to the best available model
        # Depth decay reduces the threshold by 10% per depth level
        depth_decay = max(0.5, 1.0 - (0.10 * depth))
        dynamic_threshold = max(T_FLOOR, max_confidence * 0.80 * depth_decay)
        
        dbg(f"  -> Dynamic Split Threshold set to: {dynamic_threshold:.2f}", color="cyan")

        # 7. Build optimal orchestration sequence
        sequence = []
        split_inserted = False
        
        # Calculate how many verifiers we MUST try before we are allowed to split
        min_verifiers_to_try = 0
        
        for i, (v_name, prob) in enumerate(sorted_verifiers):
            # Always try the top minimum verifiers, OR if they beat the dynamic threshold
            if i < min_verifiers_to_try or prob >= dynamic_threshold:
                sequence.append(v_map[v_name])
            else:
                # We met the depth quota AND confidence is low -> Time to split!
                if not split_inserted:
                    sequence.append('split')
                    split_inserted = True
                sequence.append(v_map[v_name])
                
        # Edge case: All verifiers were evaluated without triggering a split
        if not split_inserted:
            sequence.append('split')
            
        return sequence

    except Exception as e:
        dbg(f"[PREDICTOR] Critical Error: {str(e)}. Falling back to default sequence.", color="red")
        return [v_map['se'], v_map['pa'], 'split', v_map['ki'], v_map['bmc']]

# --- 2. SEQUENTIAL CHECK ENGINE ---

class SequentialCheckTask(ContinuationTask):
    """
    Recursively runs a list of verifiers one by one.
    If a verifier fails, it spawns the next one. If all fail, it returns the split tuple.
    """
    def __init__(self, verifiers_to_run, inputfile, depth, split_parent=None):
        self._verifiers_to_run = verifiers_to_run
        self._inputfile = inputfile
        self._depth = depth
        self.split_parent = split_parent
        
        if not self._verifiers_to_run:
            # The predictor asked to split immediately (0 verifiers to run)
            super().__init__(IdentityTask([]), name="SeqEmpty")
        else:
            # Run the first verifier in the remaining sequence
            current_verifier = self._verifiers_to_run[0]
            super().__init__(current_verifier(self._inputfile), name="SeqRun")

    # ADD THIS METHOD
    def inputfile(self):
        return self._inputfile
            
    def continuation(self, result):
        # Base case 1: We had no verifiers to run, trigger the split immediately
        if not self._verifiers_to_run:
            return TaskResult("DONE", [(self._inputfile, self._depth)], task=self)
            
        # Base case 2: The current verifier succeeded or found a bug!
        if task_result_is_conclusive(result):
            return result
            
        # The current verifier failed/timed out. 
        # Check if we have more verifiers in our sequence list.
        remaining_verifiers = self._verifiers_to_run[1:]
        
        if not remaining_verifiers:
            # We exhausted the sequential list and all failed. Trigger the split.
            return TaskResult("DONE", [(self._inputfile, self._depth)], task=self)
        else:
            # Recursively replace this task with a new SequentialCheckTask for the next verifier!
            return TaskResult(
                "REPLACE_TASK", 
                SequentialCheckTask(remaining_verifiers, self._inputfile, self._depth, self.split_parent)
            )


# --- 3. PREDICTIVE POLICY ENFORCER ---

class PredictiveCheckTask(ContinuationTask):
    def __init__(self, verifiers, predictor, inputfile, depth=0, split_parent=None, lineage_state=None):
        self._verifiers = verifiers
        self._predictor = predictor
        self._inputfile = inputfile
        self._depth = depth
        self.split_parent = split_parent
        
        # 1. Initialize or inherit the lineage state
        self.lineage_state = lineage_state or {'punish': [], 'reward': []}

        # 2. Ask predictor for the policy sequence, passing the state!
        actions = self._predictor(self._inputfile, self._verifiers, self._depth, self.lineage_state)
        
        if 'split' not in actions:
            raise ValueError(f"[FATAL] Predictor did not return a 'split' token...")
            
        split_index = actions.index('split')
        self.verifiers_to_run = actions[:split_index] # Save this to know who failed
        
        str_names = [v.__class__.__name__ for v in self.verifiers_to_run]
        dbg(f"[{self._inputfile.path} | Depth {self._depth}] Execution Plan: {' -> '.join(str_names)} -> SPLIT", color="magenta")

        super().__init__(
            SequentialCheckTask(self.verifiers_to_run, self._inputfile, self._depth, split_parent=split_parent), 
            name="PredictivePolicyEnforcer"
        )
    
    def inputfile(self):
        return self._inputfile
    
    def continuation(self, result):
        # If the result is "DONE" but not conclusive, the sequential verifiers failed.
        if result.is_done() and not task_result_is_conclusive(result):
            
            # Map the instantiated verifier objects back to their exact predictor keys
            # based on the strict Bubaak initialization order: [PA, KI, SE, BMC]
            v_map_reverse = {
                self._verifiers[0]: 'pa',
                self._verifiers[1]: 'ki',
                self._verifiers[2]: 'se',
                self._verifiers[3]: 'bmc'
            }
            
            # Extract the exact string keys of the verifiers that failed
            failed_keys = [v_map_reverse.get(v, 'unknown') for v in self.verifiers_to_run] 
            
            # Inherit existing punishments and add the new ones safely
            new_punishments = list(set(self.lineage_state.get('punish', []) + failed_keys))
            new_state = {'punish': new_punishments, 'reward': self.lineage_state.get('reward', [])}
            
            # Repackage the Bubaak TaskResult tuple from (file, depth) to (file, depth, state)
            file_obj, depth = result.output[0]
            return TaskResult("DONE", [(file_obj, depth, new_state)], task=result.task)
            
        return result


# --- 4. PREDICTIVE SPLIT AND CHECK TASK ---

class PredictiveSplitAndCheckTask(ContinuationTask):
    def __init__(self, verifiers, predictor, inputfile, depth=0, split_config=None, split_parent=None, lineage_state=None):
        super().__init__(SplitTask(inputfile, options=split_config), name="PredictiveSplitAndCont")
        self._verifiers = verifiers
        self._predictor = predictor
        self._inputfile = inputfile
        self._depth = depth
        self.split_parent = split_parent
        self.lineage_state = lineage_state # Store it

    def inputfile(self):
        return self._inputfile
    
    def continuation(self, result):
        if result.is_done():
            # Pass the immutable state dictionary to both children
            return TaskResult(
                "REPLACE_TASK",
                ParallelCompose([
                    PredictiveCheckTask(self._verifiers, self._predictor, result.output[0], depth=self._depth + 1, split_parent=self, lineage_state=self.lineage_state),
                    PredictiveCheckTask(self._verifiers, self._predictor, result.output[1], depth=self._depth + 1, split_parent=self, lineage_state=self.lineage_state),
                ])
            )
        return TaskResult("DONE", [TaskResult("DONE", [(self._inputfile, self._depth)], task=self)], task=self)


# --- 5. PREDICTIVE SPLITTING VERIFIER & SCHEDULER ---

class PredictiveSplittingVerificationTask(ContinuationTask):
    def __init__(self, verifiers, predictor, inputfile, max_width=2, max_height=32, 
                 unroll_limit=-1, clone_limit=-1, deepening=False, split_line_limit=100000, 
                 parallelization_limit=-1, timeout=-1):
        super().__init__(
            ParallelCompose([PredictiveCheckTask(verifiers, predictor, inputfile, depth=0)]),
            name="PredictiveSplitScheduler"
        )
        
        self._inputfile = inputfile
        self._verifiers = verifiers
        self._predictor = predictor

        self._open_tasks = []
        self._executed_tasks = set()
        self._results = []
        self._steps = 0
        self._start_time = time.clock_gettime(time.CLOCK_REALTIME)

        self._split_config = {
            "splitting": max_width,
            "steps_limit": max_height,
            "loop_unrolls": unroll_limit,
            "function_clones": clone_limit,
            "deepening": deepening,
            "split_line_limit": split_line_limit
        }

        self._parallelization_limit = max_width if parallelization_limit == -1 else parallelization_limit
        self._split_timeout = timeout 

    def _stop_splitter(self):
        step_limit = self._split_config["steps_limit"]
        if step_limit >= 0 and self._steps >= self._split_config["steps_limit"]:
            dbg(f"[ABORT-SPLIT] Reached step limit of {step_limit} steps.", color="green")
            return True
        
        split_limit = self._split_config["splitting"]
        if split_limit >= 0 and len(self._open_tasks) >= self._split_config["splitting"]:
            dbg(f"[ABORT-SPLIT] Reached split limit of {split_limit} splits.", color="green")
            return True
        
        if self._split_timeout > 0:
            current_time = time.clock_gettime(time.CLOCK_REALTIME) - self._start_time
            if current_time >= self._split_timeout:
                dbg(f"[ABORT-SPLIT] Reached soft timeout of {current_time:.3f} seconds.", color="green")
                return True
        return False

    def continuation(self, result):
        if not result.is_done(): return result

        for task_result in result.output:
            for sub_result in task_result.output:
                # Update tuple check to expect 2 OR 3 items
                if isinstance(sub_result, tuple) and len(sub_result) >= 2 and isinstance(sub_result[0], CompilationUnit):
                    self._open_tasks.append(task_result)
                elif isinstance(sub_result, Verdict):
                    if sub_result.is_incorrect(): 
                        return DynamicSplitResult([task_result], task=self)
                    self._results.append(task_result)

        if len(self._open_tasks) == 0:
            return DynamicSplitResult(self._results, task=self)
        
        self._steps += 1

        if self._stop_splitter():
            open_tasks = self._open_tasks
            dbg(f"Stopped predicting/splitting. {len(open_tasks)} unsolved parts remaining.", color="green")
            
            fallback_prp = self._verifiers[0].property
            open_tasks_verdicts = [
                TaskResult("DONE", output=[Verdict(Verdict.UNKNOWN, prp=fallback_prp)], task=open_task.task) 
                for open_task in open_tasks
            ]
            return DynamicSplitResult(self._results + open_tasks_verdicts, splits=[t.output[0][0] for t in open_tasks], task=self)

        split_check_limit = max(1, self._parallelization_limit // 2)
        exec_tasks = self._open_tasks[:split_check_limit]

        parallel_tasks = []
        for task in exec_tasks:
            # Unpack the 3-item tuple safely
            sub_res = task.output[0]
            file_obj = sub_res[0]
            file_depth = sub_res[1]
            lineage = sub_res[2] if len(sub_res) > 2 else {'punish': [], 'reward': []}

            if file_obj not in self._executed_tasks:
                parallel_tasks.append(
                    PredictiveSplitAndCheckTask(
                        self._verifiers, self._predictor, file_obj, depth=file_depth,
                        split_config=self._split_config, split_parent=task.task,
                        lineage_state=lineage # Pass the state down!
                    )
                )
            else:
                from bbk.dbg import print_stdout
                print_stdout(f"[ABORT-SPLIT] NO more splitting locations available for {file_obj.path}. Moving to residual pool.", color="yellow")
                
                # 1. Convert the file to an UNKNOWN Verdict so the merger can see it!
                fallback_prp = self._verifiers[0].property
                fake_result = TaskResult(
                    "DONE", 
                    output=[Verdict(Verdict.UNKNOWN, prp=fallback_prp)], 
                    task=task.task
                )
                self._results.append(fake_result)
        
        self._open_tasks = self._open_tasks[split_check_limit:]
        self._executed_tasks |= set(t.output[0][0] for t in exec_tasks) 

        # 2. FIX: Prevent the 'Aggregation returned None' crash!
        # If all tasks were unsplittable, parallel_tasks is empty. 
        # Bypass ComposeResults completely and recursively process the next batch.
        if not parallel_tasks:
            return self.continuation(TaskResult("DONE", []))

        return TaskResult(
            "REPLACE_TASK", ContinuationTask(ComposeResults(parallel_tasks), continuation=self.continuation)
        )


class PredictiveSplittingVerifier(SoftwareVerifier):
    def __init__(self, verifiers, predictor, max_width=2, max_height=32, 
                 unroll_limit=-1, clone_limit=-1, deepening=False, split_line_limit=100000,
                 timeout=-1, parallelization_limit=-1):
        super().__init__(verifiers[0].args, verifiers[0].property)
        self.verifiers = verifiers
        self.predictor = predictor
        
        self.max_width = max_width
        self.max_height = max_height
        self.unroll_limit = unroll_limit
        self.clone_limit = clone_limit
        self.deepening = deepening
        self.split_line_limit = split_line_limit
        self.split_timeout = timeout
        self.parallelization_limit = parallelization_limit

    def create_task(self, inputfile: CompilationUnit):
        return PredictiveSplittingVerificationTask(
            self.verifiers, self.predictor, inputfile, 
            max_width=self.max_width, max_height=self.max_height, 
            unroll_limit=self.unroll_limit, clone_limit=self.clone_limit,
            deepening=self.deepening, split_line_limit=self.split_line_limit,
            timeout=self.split_timeout, parallelization_limit=self.parallelization_limit,
        )


# --- 6. PERIODIC MERGE TASK (Orchestrator) ---

class PeriodicMergeTask(ContinuationTask):
    def __init__(self, verifiers, predictor, programs, properties, split_timeout=200, max_width=4, max_height=32):
        self.verifiers = verifiers
        self.predictor = predictor
        self.programs = programs
        self.properties = properties
        self.split_timeout = split_timeout
        self.max_width = max_width
        self.max_height = max_height
        
        verifier = PredictiveSplittingVerifier(
            verifiers=self.verifiers, 
            predictor=self.predictor,
            timeout=self.split_timeout, 
            max_width=self.max_width,
            max_height=self.max_height
        )
        
        super().__init__(verifier.create_batch(self.programs), name="PeriodicMergeTask")

    def continuation(self, result):
        if task_result_is_conclusive(result) or not hasattr(result, 'splits'):
            return result.verdicts() if hasattr(result, 'verdicts') else result
            
        open_splits = result.splits() 
        
        if not open_splits or len(open_splits) <= 1:
            return result.verdicts() if hasattr(result, 'verdicts') else result

        print_stdout(f"----------\n[MERGE SIGNAL] {self.split_timeout}s period reached. Merging {len(open_splits)} splits into a residual program...\n----------", color="yellow")
        
        # 1. Execute the synchronous merge
        merge_result = cpa_merge(result)
        
        # 2. Extract the new residual program directly
        if not merge_result.is_done() or not hasattr(merge_result, 'splits'):
            print_stdout("[MERGE FAILED] Could not synthesize residual program. Returning current verdicts.", color="red")
            return result.verdicts() if hasattr(result, 'verdicts') else result
            
        residual_splits = merge_result.splits()
        
        if not residual_splits:
            print_stdout("[MERGE FAILED] No residual program generated. Returning verdicts.", color="red")
            return result.verdicts() if hasattr(result, 'verdicts') else result
            
        residual_program = residual_splits[0]
        
        print_stdout(f"[MERGE SUCCESS] Restarting predictive loop on residual: {residual_program.path}", color="green")
        
        # 3. Feed the new residual program back into our dynamic Predictor!
        return TaskResult(
            "REPLACE_TASK", 
            PeriodicMergeTask(
                self.verifiers, self.predictor, [residual_program], 
                self.properties, self.split_timeout, self.max_width, self.max_height
            )
        )