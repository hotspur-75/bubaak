/*
 * algorithm for computing simultaneously the GCD and the LCM,
 * by Sankaranarayanan
 */

extern void abort(void);
extern void __assert_fail(const char *, const char *, unsigned int, const char *) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "lcm1.c", 8, "reach_error"); }
extern unsigned __VERIFIER_nondet_uint(void);
extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}
void __VERIFIER_assert(int cond) {
    if (!(cond)) {
    ERROR:
        {reach_error();}
    }
    return;
}

int main() {
    int z = 10;
    int y = 15;
    if (y == 15) {
        z = 20;
    }
    else {
        z = 49;
    }
    __VERIFIER_assert(z == y);
    return 0;
}