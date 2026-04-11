import code_ast as ca

from pycpa.graph import ControlFlowGraph
from pycpa.cfa import ControlFlowAutomata

from pycpa.splitting import run_splitter
from pycpa.merge     import run_merger

from pycpa.compose import is_subset, trace, intersect, negate


def cfa(program):
    ast = ca.ast(program.strip(), lang = "c", syntax_error = "raise")
    root_node = ast.root_node()
    cfg_graph = ControlFlowGraph(root_node)
    return ControlFlowAutomata(cfg_graph)


def _test_split_integrity(program):
    source_automata = cfa(program)

    splits = run_splitter(source_automata)
    assert len(splits) == 2

    left, right = splits

    source_traces = trace(source_automata)

    assert is_subset(trace(left), source_traces), intersect(trace(left), negate(source_traces)).to_dot()
    assert is_subset(trace(right), source_traces), intersect(trace(right), negate(source_traces)).to_dot()

    assert not is_subset(source_traces, trace(left))
    assert not is_subset(source_traces, trace(right))


def _test_merge_integrity(program):
    source_automata = cfa(program)

    splits = run_splitter(source_automata)
    assert len(splits) == 2

    left, right = splits

    merge_automata = cfa(run_merger(left, right))

    print(merge_automata.root_ast_node.text.decode('utf-8'))

    merge_traces   = trace(merge_automata)
    assert is_subset(trace(left), merge_traces)
    assert is_subset(trace(right), merge_traces)

    assert not is_subset(merge_traces, trace(left))
    assert not is_subset(merge_traces, trace(right))


# Split integrity ----------------------------------------------------


def test_split_integrity_1():
    test = """
int main(){
    int a = 0;
    int b = 1;
    
    if(a < b){
        a = b;
    }

}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)


def test_split_integrity_2():
    test = """
int main(){
    int a = 0;
    int b = 1;
    
    if(a < b){
        a = b;
    }

    if(a != b){
        reach_error();
    }

}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)


def test_split_integrity_3():
    test = """
int main(){
    int a = 0;
    int b = 1;
    
    while(a < b){
        a++;
    }

    if(a != b){
        reach_error();
    }

}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)

# Test sv-benchmark -----------------------

def test_svcomp_1():
    test = """
extern void abort(void);
extern void __assert_fail(const char *, const char *, unsigned int, const char *) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "count_up_down-1.c", 3, "reach_error"); }

void __VERIFIER_assert(int cond) {
  if (!(cond)) {
    ERROR: {reach_error();abort();}
  }
  return;
}
unsigned int __VERIFIER_nondet_uint();

int main()
{
  unsigned int n = __VERIFIER_nondet_uint();
  unsigned int x=n, y=0;
  while(x>0)
  {
    x--;
    y++;
  }
  __VERIFIER_assert(y==n);
}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)


def test_svcomp_2():
    test = """
extern void abort(void);
extern void __assert_fail(const char *, const char *, unsigned int, const char *) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "array-1.c", 3, "reach_error"); }

void __VERIFIER_assert(int cond) {
  if (!(cond)) {
    ERROR: {reach_error();abort();}
  }
  return;
}
int __VERIFIER_nondet_int();

int main()
{
  unsigned int SIZE=1;
  unsigned int j,k;
  int array[SIZE], menor;
  
  menor = __VERIFIER_nondet_int();

  for(j=0;j<SIZE;j++) {
       array[j] = __VERIFIER_nondet_int();
       
       if(array[j]<=menor)
          menor = array[j];                          
    }                       
    
    __VERIFIER_assert(array[0]>=menor);    

    return 0;
}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)


def test_svcomp_3():
    test = """
extern void abort(void);

extern void __assert_fail (const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
extern void __assert_perror_fail (int __errnum, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
extern void __assert (const char *__assertion, const char *__file, int __line)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));

void reach_error() { ((void) sizeof ((0) ? 1 : 0), __extension__ ({ if (0) ; else __assert_fail ("0", "verisec_OpenSER_cases1_stripFullBoth_arr.c", 3, __extension__ __PRETTY_FUNCTION__); })); }
void __VERIFIER_assert(int cond) {
  if (!(cond)) {
    ERROR: {reach_error();abort();}
  }
  return;
}
typedef unsigned int size_t;
typedef int bool;
char *strchr(const char *s, int c);
char *strrchr(const char *s, int c);
char *strstr(const char *haystack, const char *needle);
char *strncpy (char *dest, const char *src, size_t n);
char *strncpy_ptr (char *dest, const char *src, size_t n);
char *strcpy (char *dest, const char *src);
unsigned strlen(const char *s);
int strncmp (const char *s1, const char *s2, size_t n);
int strcmp (const char *s1, const char *s2);
char *strcat(char *dest, const char *src);
void *memcpy(void *dest, const void *src, size_t n);
int isascii (int c);
int isspace (int c);
int getc ( );
char *strrand (char *s);
int istrrand (char *s);
int istrchr(const char *s, int c);
int istrrchr(const char *s, int c);
int istrncmp (const char *s1, int start, const char *s2, size_t n);
int istrstr(const char *haystack, const char *needle);
char *r_strncpy (char *dest, const char *src, size_t n){return strncpy(dest,src,n);}
char *r_strcpy (char *dest, const char *src);
char *r_strcat(char *dest, const char *src);
char *r_strncat(char *dest, const char *src, size_t n);
void *r_memcpy(void *dest, const void *src, size_t n);
typedef unsigned int u_int;
typedef unsigned char u_int8_t;
struct ieee80211_scan_entry {
  u_int8_t *se_rsn_ie;
};
typedef int NSS_STATUS;
typedef char fstring[2];
struct sockaddr_un
{
  char sun_path[2 + 1];
};
static int parse_expression_list(char *str)
{
  int start=0, i=-1, j=-1;
  char str2[2];
  if (!str) return -1;
  do {
    i++;
    switch(str[i]) {
    case 0:
      while ((str[start] == ' ') || (str[start] == '\t')) start++;
      if (str[start] == '"') start++;
      j = i-1;
      while ((0 < j) && ((str[j] == ' ') || (str[j] == '\t'))) j--;
      if ((0 < j) && (str[j] == '"')) j--;
      if (start<=j) {
        r_strncpy(str2, str+start, j-start+1);
        __VERIFIER_assert(j - start + 1 < 2);
        str2[j-start+1] = 0;
      } else {
        return -1;
      }
      start = i+1;
    }
  } while (str[i] != 0);
  return 0;
}
int main ()
{
  char A [2 + 2 + 4 +1];
  A[2 + 2 + 4] = 0;
  parse_expression_list (A);
  return 0;
}

"""

    _test_split_integrity(test)
    _test_merge_integrity(test)


def test_svcomp_4():
    test = """
extern void abort(void);
extern void __assert_fail(const char *, const char *, unsigned int, const char *) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "array30_pattern.c", 24, "reach_error"); }
extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}
void __VERIFIER_assert(int cond) { if(!(cond)) { ERROR: {reach_error();abort();} } }
extern int __VERIFIER_nondet_int() ;
extern short __VERIFIER_nondet_short() ;

signed long long ARR_SIZE ;

int diff(short idx1 ,short idx2)
{
        if(idx1 > idx2)
                return (idx1 - idx2) ;
        else
                return (idx2 - idx1) ;
}

int main()
{
        ARR_SIZE = (signed long long)__VERIFIER_nondet_short() ;
        assume_abort_if_not(ARR_SIZE > 0) ;

        int array[ARR_SIZE][ARR_SIZE] ;
        
        int row = 0, column = 0 ;
        signed long long sum = 0 ;

        for(row=0;row<ARR_SIZE;row++)
                for(column=0;column<ARR_SIZE;column++)
                        array[row][column] = diff(row,column) ;
                                

        for(row=0;row<ARR_SIZE;row++)
                for(column=0;column<ARR_SIZE;column++)
                        sum = sum + array[row][column] ;

        __VERIFIER_assert(3*sum == (ARR_SIZE*(ARR_SIZE-1)*(ARR_SIZE+1))) ;
        return 0 ;
}
"""
    _test_split_integrity(test)
    _test_merge_integrity(test)


# Test ECA -------------------------------


