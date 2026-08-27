package io.jmetervsghz.sut.config;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Scheduler used by {@code Compute} to delay responses without blocking a gRPC
 * worker thread.
 *
 * <p>This matters for benchmark fairness: if the delay were implemented with
 * {@code Thread.sleep} on the handler thread, the server would run out of
 * threads long before either load generator reached its own limit, and the
 * benchmark would measure the server instead of the tools.
 */
@Configuration
public class SchedulerConfig {

  @Bean(destroyMethod = "shutdownNow")
  public ScheduledExecutorService delayScheduler(
      @Value("${benchmark.delay-scheduler-threads:4}") int threads) {
    return Executors.newScheduledThreadPool(
        threads,
        runnable -> {
          Thread thread = new Thread(runnable, "delay-scheduler");
          thread.setDaemon(true);
          return thread;
        });
  }
}
